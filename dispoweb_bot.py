#!/usr/bin/env python3
"""
Bot de surveillance des creneaux d'examen pratique moto sur DispoWeb (ge.ch).

IMPORTANT - a lire avant utilisation
-------------------------------------
- Ce script utilise TES identifiants (NIP FABER + date de naissance), lus
  uniquement depuis le fichier local .env que TU remplis toi-meme. Personne
  d'autre que toi ne les saisit ou ne les voit.
- Les noms d'endpoints ci-dessous ont ete deduits en lisant le code
  JavaScript public du site (pas de connexion effectuee pendant l'analyse).
  Ils sont marques [A VERIFIER] car leur prefixe exact ainsi que les codes
  categorie/genre/type pour la moto n'ont pas pu etre confirmes sans
  connexion reelle. Lance d'abord `python dispoweb_bot.py --discover` pour
  les calibrer (voir README.md).
- Ce site est un service public cantonal. Un usage automatise agressif
  (polling trop frequent, reservation en boucle) peut violer ses conditions
  d'utilisation et/ou faire bloquer ton IP ou ton compte. Garde un intervalle
  raisonnable (>= 3-5 minutes) et n'utilise pas ce bot pour bloquer plusieurs
  creneaux a la fois.
- Le mode reservation automatique (AUTO_BOOK=true) engage une action reelle
  et potentiellement difficile a annuler (occupation d'un creneau, delais
  d'annulation). Teste d'abord en mode notification seule.
"""

import os
import sys
import json
import time
import random
import hashlib
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

ZURICH = ZoneInfo("Europe/Zurich")


def date_base_for(local_date) -> str:
    """Convertit une date locale (Europe/Zurich) minuit en dateBase UTC attendu par l'API."""
    dt_local = datetime(local_date.year, local_date.month, local_date.day, tzinfo=ZURICH)
    dt_utc = dt_local.astimezone(timezone.utc)
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def monday_of(local_date):
    return local_date - timedelta(days=local_date.weekday())


def epoch_ms_to_date(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ZURICH).date()


try:
    from plyer import notification as desktop_notification
except Exception:
    desktop_notification = None

BASE = "https://ge.ch/tradispoweb_public"

# --- [A VERIFIER] endpoints deduits du JS public, a confirmer/corriger ---
# via `--discover` ou l'onglet Reseau du navigateur (voir README.md)
LOGIN_URL = f"{BASE}/login/loginConduitePrive/fr"
ACTION_URL = f"{BASE}/rendez-vous/action"
LISTE_LIEU_URL = f"{BASE}/lieu-examen/listeLieu"
LISTE_JOUR_HEURE_LIBRE_URL = f"{BASE}/lieu-examen/listeJourHeureLibre"
CHOIX_RDV_URL = f"{BASE}/rendez-vous/choixRdv"
PRISE_RDV_URL = f"{BASE}/rendez-vous/priseRdv"
LISTE_RDV_URL = f"{BASE}/rendez-vous/listeRdvPrive"

STATE_FILE = Path(__file__).parent / "seen_slots.json"
LOG_FILE = Path(__file__).parent / "dispoweb_bot.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dispoweb")


class Config:
    def __init__(self):
        load_dotenv(Path(__file__).parent / ".env")
        self.nip = os.environ.get("DISPOWEB_NIP", "").strip()
        self.birthday = os.environ.get("DISPOWEB_BIRTHDAY", "").strip()
        self.categorie = os.environ.get("DISPOWEB_CATEGORIE", "A").strip()
        self.id_groupe_examen = os.environ.get("DISPOWEB_ID_GROUPE_EXAMEN", "").strip()
        self.genre = os.environ.get("DISPOWEB_GENRE", "").strip()
        self.type_examen = os.environ.get("DISPOWEB_TYPE", "").strip()
        lieux = os.environ.get("DISPOWEB_LIEUX", "").strip()
        self.lieux = [x.strip() for x in lieux.split(",") if x.strip()] if lieux else []
        self.poll_interval = int(os.environ.get("DISPOWEB_POLL_INTERVAL", "240"))
        self.auto_book = os.environ.get("DISPOWEB_AUTO_BOOK", "false").lower() == "true"
        # Semaines proches revues a chaque cycle (POLL_INTERVAL)
        self.near_weeks = int(os.environ.get("DISPOWEB_NEAR_WEEKS", "4"))
        # Pas (en semaines) entre deux points de controle lointains
        self.far_step_weeks = int(os.environ.get("DISPOWEB_FAR_STEP_WEEKS", "2"))
        # Frequence (secondes) du balayage complet jusqu'a l'horizon
        self.far_scan_interval = int(os.environ.get("DISPOWEB_FAR_SCAN_INTERVAL", "3600"))
        # Horizon maximum fiable (au-dela, le site n'a pas encore ouvert la fenetre
        # et l'API peut renvoyer un calendrier par defaut trompeur, pas le vrai statut)
        self.max_weeks_ahead = int(os.environ.get("DISPOWEB_MAX_WEEKS_AHEAD", "17"))
        # Pause entre deux requetes consecutives (politesse envers le serveur)
        self.request_delay = float(os.environ.get("DISPOWEB_REQUEST_DELAY", "1.5"))
        # Notification WhatsApp via CallMeBot (optionnel, voir README.md)
        self.whatsapp_phone = os.environ.get("WHATSAPP_PHONE", "").strip()
        self.whatsapp_apikey = os.environ.get("WHATSAPP_APIKEY", "").strip()
        # TEST UNIQUEMENT: force la date limite de notification (format JJ.MM.AAAA)
        # au lieu de la date de ton rendez-vous existant, detectee automatiquement.
        cutoff_str = os.environ.get("DISPOWEB_CUTOFF_OVERRIDE", "").strip()
        self.cutoff_override = datetime.strptime(cutoff_str, "%d.%m.%Y").date() if cutoff_str else None

        if not self.nip or not self.birthday:
            log.error("DISPOWEB_NIP / DISPOWEB_BIRTHDAY manquants. Copie .env.example vers .env et remplis-le.")
            sys.exit(1)


def notify(title: str, message: str, cfg: "Config | None" = None):
    log.info(f"NOTIFICATION: {title} - {message}")
    print("\a", end="")  # beep terminal
    if desktop_notification:
        try:
            desktop_notification.notify(title=title, message=message, timeout=20)
        except Exception as e:
            log.warning(f"Notification desktop echouee: {e}")
    if cfg and cfg.whatsapp_phone and cfg.whatsapp_apikey:
        try:
            requests.get(
                "https://api.callmebot.com/whatsapp.php",
                params={"phone": cfg.whatsapp_phone, "text": f"{title} - {message}", "apikey": cfg.whatsapp_apikey},
                timeout=15,
            )
        except Exception as e:
            log.warning(f"Notification WhatsApp echouee: {e}")


class DispoWebClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; personal-appointment-watcher/1.0)",
        })
        # ID de ton rendez-vous existant, requis par le site pour voir les vraies
        # disponibilites (confirme par capture reseau). Renseigne par mes_rdv().
        # Lecture seule: n'appelle jamais l'action "move", juste enrichit la requete.
        self.rendezvous_id = None
        self.rendezvous_date = None
        self.rendezvous_heure = None
        self.rendezvous_minute = None
        # Date limite utilisee pour filtrer les notifications - egale a
        # rendezvous_date par defaut, sauf si DISPOWEB_CUTOFF_OVERRIDE est defini
        # (pour des tests controles uniquement).
        self.notify_cutoff_date = None

    def login(self):
        payload = {
            "noRegistre": self.cfg.nip,
            "dateNaissance": self.cfg.birthday,
            "captcha": "",
        }
        r = self.session.post(LOGIN_URL, json=payload, timeout=20)
        if r.status_code == 401 or r.status_code == 403:
            raise RuntimeError(
                f"Login refuse (HTTP {r.status_code}). Verifie NIP/date de naissance, "
                f"ou le site exige peut-etre un captcha maintenant. Reponse: {r.text[:300]}"
            )
        r.raise_for_status()
        log.info("Connexion reussie.")
        return r.json()

    def init_rdv_context(self):
        payload = {
            "action": "new",
            "cat": self.cfg.categorie,
            "genre": self.cfg.genre,
            "type": self.cfg.type_examen,
        }
        if self.cfg.id_groupe_examen:
            payload["idGroupeExamen"] = self.cfg.id_groupe_examen
        return self._post_json(ACTION_URL, payload)

    def mes_rdv(self):
        """Liste tes rendez-vous existants. Renseigne aussi self.rendezvous_id,
        requis par listeJourHeureLibre pour voir les vraies disponibilites
        (confirme par capture reseau). Lecture seule - n'appelle jamais 'move'."""
        r = self.session.get(LISTE_RDV_URL, timeout=20)
        r.raise_for_status()
        try:
            data = r.json()
        except ValueError:
            raise RuntimeError(f"Reponse non-JSON de {LISTE_RDV_URL} (status {r.status_code}): {r.text[:300]!r}")
        items = data if isinstance(data, list) else (data.get("listeRdvPrive") or [])
        if items:
            self.rendezvous_id = items[0].get("rendezVous") or items[0].get("id") or items[0].get("idRendezVous")
            jour_ms = items[0].get("jour")
            if jour_ms:
                self.rendezvous_date = epoch_ms_to_date(jour_ms)
            self.rendezvous_heure = items[0].get("heure")
            self.rendezvous_minute = items[0].get("minute")
        self.notify_cutoff_date = self.rendezvous_date
        if self.cfg.cutoff_override:
            self.notify_cutoff_date = self.cfg.cutoff_override
            log.warning(f"DISPOWEB_CUTOFF_OVERRIDE actif: date limite de notification forcee a {self.cfg.cutoff_override} (test uniquement).")
        return data

    def init_move_context(self, rendezvous_id):
        """Etablit le contexte 'deplacer un rendez-vous existant' au lieu de 'nouvelle inscription' -
        c'est probablement ce qu'il faut utiliser puisque tu as deja un RDV paye a modifier, pas a dupliquer."""
        payload = {
            "action": "move",
            "item": rendezvous_id,
            "cat": self.cfg.categorie,
            "genre": self.cfg.genre,
            "type": self.cfg.type_examen,
        }
        if self.cfg.id_groupe_examen:
            payload["idGroupeExamen"] = self.cfg.id_groupe_examen
        return self._post_json(ACTION_URL, payload)

    def list_lieux(self):
        filtre = {
            "idGroupeExamen": self.cfg.id_groupe_examen,
            "idCategorie": self.cfg.categorie,
            "idGenreExamen": self.cfg.genre,
            "idTypeExamen": self.cfg.type_examen,
        }
        return self._post_json(LISTE_LIEU_URL, {"typeCategorieLieuDispo": filtre})

    def _post_json(self, url, payload):
        r = self.session.post(url, json=payload, timeout=20)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            snippet = r.text[:300].replace("\n", " ")
            raise RuntimeError(
                f"Reponse non-JSON de {url} (status {r.status_code}, "
                f"content-type {r.headers.get('content-type')}). "
                f"Debut de la reponse: {snippet!r} -- l'URL est probablement fausse, "
                f"a corriger via le Network tab du navigateur."
            )

    def list_creneaux(self, id_lieu_examen, date_base_iso):
        body = {
            "typeCategorieLieuDispo": {
                "dateBase": date_base_iso,
                "idLieuExamen": id_lieu_examen,
                "idGroupeExamen": self.cfg.id_groupe_examen,
                "idCategorie": self.cfg.categorie,
                "idGenreExamen": self.cfg.genre,
                "idTypeExamen": self.cfg.type_examen,
            }
        }
        if self.rendezvous_id:
            body["rendezVous"] = self.rendezvous_id
        return self._post_json(LISTE_JOUR_HEURE_LIBRE_URL, body)

    def choisir_et_reserver(self, slot: dict, id_lieu_examen):
        # [A VERIFIER] Format jamais teste en conditions reelles (aucun vrai
        # creneau proche disponible au moment de l'ecriture). Deduit par
        # analogie avec listeJourHeureLibre. A ajuster si le site renvoie
        # une erreur au premier vrai essai - regarder le Network tab en cas
        # d'echec pour comparer avec ce qui est envoye ici.
        payload = {
            "jour": slot.get("jour"),
            "heure": slot.get("heure"),
            "minute": slot.get("minute"),
            "typeCategorieLieuDispo": {
                "idLieuExamen": id_lieu_examen,
                "idGroupeExamen": self.cfg.id_groupe_examen,
                "idCategorie": self.cfg.categorie,
                "idGenreExamen": self.cfg.genre,
                "idTypeExamen": self.cfg.type_examen,
            },
        }
        choix = self._post_json(CHOIX_RDV_URL, payload)
        return self._post_json(PRISE_RDV_URL, {})


def load_seen():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(seen)), encoding="utf-8")


def slot_key(lieu_id, slot) -> str:
    raw = json.dumps({"lieu": lieu_id, "slot": slot}, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def run_discover(client: DispoWebClient, cfg: Config):
    print("\n=== MODE DISCOVER ===")
    print("Connexion...")
    print(json.dumps(client.login(), indent=2, ensure_ascii=False)[:2000])

    print("\nTes rendez-vous existants (listeRdvPrive)...")
    try:
        mes_rdv = client.mes_rdv()
        print(json.dumps(mes_rdv, indent=2, ensure_ascii=False)[:3000])
        if client.rendezvous_id:
            print(f"=> rendezVous existant detecte: {client.rendezvous_id} (utilise automatiquement dans les recherches ci-dessous)")
    except Exception as e:
        print(f"Erreur mes_rdv: {e}")

    print("\nInit contexte RDV nouvelle inscription (lecture seule - 'move' n'est jamais appele)...")
    try:
        print(json.dumps(client.init_rdv_context(), indent=2, ensure_ascii=False)[:2000])
    except Exception as e:
        print(f"Erreur init_rdv_context: {e}")

    print("\nListe des lieux...")
    lieu_id = None
    try:
        lieux = client.list_lieux()
        print(json.dumps(lieux, indent=2, ensure_ascii=False)[:3000])
        if isinstance(lieux, list) and lieux:
            lieu_id = lieux[0].get("idLieuExamen") or lieux[0].get("id")
    except Exception as e:
        print(f"Erreur list_lieux: {e}")

    if cfg.lieux:
        lieu_id = cfg.lieux[0]
    if lieu_id:
        today = datetime.now(ZURICH).date()
        print(f"\nCreneaux libres pour lieu={lieu_id}, semaine en cours...")
        try:
            data = client.list_creneaux(lieu_id, date_base_for(monday_of(today)))
            print(json.dumps(data, indent=2, ensure_ascii=False)[:4000])
        except Exception as e:
            print(f"Erreur list_creneaux: {e}")

        print(f"\nCreneaux libres pour lieu={lieu_id}, semaine du 18.01.2027 (test cible)...")
        try:
            data2 = client.list_creneaux(lieu_id, date_base_for(monday_of(today) + timedelta(weeks=21)))
            print(json.dumps(data2, indent=2, ensure_ascii=False)[:4000])
        except Exception as e:
            print(f"Erreur list_creneaux (semaine cible): {e}")
    else:
        print("\n(Aucun idLieuExamen trouve automatiquement - mets DISPOWEB_LIEUX dans .env, "
              "ex: DISPOWEB_LIEUX=SANCAROUGE, puis relance --discover)")

    print("\n=> Copie les IDs/valeurs pertinents (idGroupeExamen, genre, type, idLieuExamen) dans .env")
    print("=> Si les URLs retournent 404, ouvre les DevTools du navigateur (F12 > Reseau) pendant")
    print("   une connexion manuelle et compare avec les URLs en haut de dispoweb_bot.py")


def scan_lieu(client: DispoWebClient, cfg: Config, id_lieu, wk_monday, seen) -> "date | None":
    """Interroge une semaine donnee, notifie/reserve les nouveaux creneaux, renvoie la date horizon (maxDate)."""
    data = client.list_creneaux(id_lieu, date_base_for(wk_monday))
    slots = data.get("listeJourHeureLibreDispo") or []
    for slot in slots:
        jour_ms = slot.get("jour")
        slot_date = epoch_ms_to_date(jour_ms) if jour_ms else None
        if client.notify_cutoff_date and slot_date and slot_date >= client.notify_cutoff_date:
            continue  # a partir de la date limite de notification - ignore silencieusement
        if (client.rendezvous_date and slot_date == client.rendezvous_date
                and slot.get("heure") == client.rendezvous_heure and slot.get("minute") == client.rendezvous_minute):
            continue  # c'est ton propre creneau deja reserve, pas une nouveaute
        key = slot_key(id_lieu, slot)
        if key not in seen:
            seen.add(key)
            heure = slot.get("heure")
            minute = slot.get("minute")
            date_txt = slot_date.strftime("%d.%m.%Y") if slot_date else "date inconnue"
            heure_txt = f"{heure:02d}:{minute:02d}" if heure is not None and minute is not None else "heure inconnue"
            msg = f"{date_txt} a {heure_txt} - lieu {id_lieu}"
            notify("Creneau moto disponible !", msg, cfg)
            if cfg.auto_book:
                try:
                    result = client.choisir_et_reserver(slot, id_lieu)
                    notify("Reservation tentee", json.dumps(result, ensure_ascii=False)[:200], cfg)
                    log.info(f"Reservation effectuee: {result}")
                except Exception as e:
                    log.error(f"Echec reservation automatique: {e}")
    max_date = data.get("maxDate")
    return epoch_ms_to_date(max_date) if max_date else None


def init_context(client: DispoWebClient, cfg: Config):
    """Recupere l'ID de ton rendez-vous existant (lecture seule, via mes_rdv) -
    requis par listeJourHeureLibre pour voir les vraies disponibilites - puis
    initialise le contexte 'nouvelle inscription'. N'appelle JAMAIS l'action
    'move': le bot ne doit jamais initier de deplacement sur le RDV reel."""
    try:
        client.mes_rdv()
        if client.rendezvous_id:
            log.info(f"Rendez-vous existant detecte (id={client.rendezvous_id}), utilise pour affiner les recherches.")
    except Exception as e:
        log.warning(f"Impossible de recuperer tes rendez-vous existants ({e}) - recherche sans cet identifiant.")
    client.init_rdv_context()


def run_one_cycle(client: DispoWebClient, cfg: Config, seen: set, do_far_scan: bool):
    today = datetime.now(ZURICH).date()
    base_monday = monday_of(today)

    for id_lieu in cfg.lieux:
        horizon = None
        near_cap = min(cfg.near_weeks, cfg.max_weeks_ahead)
        for w in range(near_cap + 1):
            horizon = scan_lieu(client, cfg, id_lieu, base_monday + timedelta(weeks=w), seen) or horizon
            time.sleep(cfg.request_delay)

        capped_horizon = min(horizon, base_monday + timedelta(weeks=cfg.max_weeks_ahead)) if horizon else None
        if do_far_scan and capped_horizon:
            w = cfg.near_weeks + cfg.far_step_weeks
            far_checkpoints = []
            while base_monday + timedelta(weeks=w) <= capped_horizon:
                far_checkpoints.append(base_monday + timedelta(weeks=w))
                w += cfg.far_step_weeks
            if far_checkpoints:
                log.info(f"[{id_lieu}] Balayage horizon lointain: {len(far_checkpoints)} semaines jusqu'a {capped_horizon} (limite configuree, limite reelle du site: {horizon})")
            for wk_monday in far_checkpoints:
                scan_lieu(client, cfg, id_lieu, wk_monday, seen)
                time.sleep(cfg.request_delay)


def run_once(client: DispoWebClient, cfg: Config):
    """Une seule verification puis on quitte - pense pour GitHub Actions (planning externe)."""
    if not cfg.lieux:
        log.error("DISPOWEB_LIEUX est vide dans .env - ajoute au moins un idLieuExamen (ex: SANCAROUGE).")
        sys.exit(1)
    seen = load_seen()
    client.login()
    init_context(client, cfg)
    try:
        run_one_cycle(client, cfg, seen, do_far_scan=True)
    except requests.exceptions.RequestException as e:
        log.warning(f"Erreur reseau en cours de scan (ignoree, etat partiel sauvegarde): {e}")
    save_seen(seen)
    log.info("Verification unique terminee.")


def run_watch(client: DispoWebClient, cfg: Config):
    seen = load_seen()
    log.info(
        f"Demarrage surveillance. Intervalle proche: {cfg.poll_interval}s "
        f"(prochaines {cfg.near_weeks} semaines). Balayage lointain toutes les "
        f"{cfg.far_scan_interval}s (pas de {cfg.far_step_weeks} semaines). Auto-book: {cfg.auto_book}"
    )
    client.login()
    init_context(client, cfg)
    if not cfg.lieux:
        log.error("DISPOWEB_LIEUX est vide dans .env - ajoute au moins un idLieuExamen (ex: SANCAROUGE).")
        return

    last_far_scan = 0.0

    while True:
        try:
            do_far_scan = (time.time() - last_far_scan) >= cfg.far_scan_interval
            run_one_cycle(client, cfg, seen, do_far_scan)
            if do_far_scan:
                last_far_scan = time.time()
            save_seen(seen)
            log.info("Cycle de surveillance termine.")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                log.warning("Session expiree, reconnexion...")
                try:
                    client.login()
                    init_context(client, cfg)
                except Exception as e2:
                    log.error(f"Reconnexion echouee: {e2}")
            else:
                log.error(f"Erreur HTTP: {e}")
        except Exception as e:
            log.error(f"Erreur inattendue: {e}")

        jitter = random.uniform(-15, 15)
        time.sleep(max(30, cfg.poll_interval + jitter))


def main():
    parser = argparse.ArgumentParser(description="Surveille les creneaux DispoWeb ge.ch")
    parser.add_argument("--discover", action="store_true", help="Affiche les reponses brutes de l'API pour calibrer .env")
    parser.add_argument("--once", action="store_true", help="Une seule verification puis quitte (pour GitHub Actions)")
    args = parser.parse_args()

    cfg = Config()
    client = DispoWebClient(cfg)

    if args.discover:
        run_discover(client, cfg)
    elif args.once:
        run_once(client, cfg)
    else:
        run_watch(client, cfg)


if __name__ == "__main__":
    main()
