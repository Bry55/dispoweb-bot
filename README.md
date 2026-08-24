# DispoWeb bot - surveillance creneaux examen pratique moto (ge.ch)

## Ce que ce bot fait
Se connecte a DispoWeb avec TES identifiants (lus depuis ton fichier `.env` local),
puis interroge periodiquement l'API pour detecter l'ouverture de nouveaux creneaux
et te notifie (son + notification bureau), avec option de reservation automatique.

## Pourquoi c'est toi qui dois entrer tes identifiants
Je (l'assistant) ne saisis jamais de mot de passe / identifiant de connexion pour
toi, meme avec ton accord - c'est une regle stricte de securite qui s'applique
meme a un site peu sensible comme celui-ci. Le script est fait pour que TU
remplisses `.env` toi-meme ; je ne l'ai pas rempli et je ne l'ai jamais vu.

## Installation

```bash
cd dispoweb-bot
pip install -r requirements.txt
copy .env.example .env
```

Puis ouvre `.env` et remplis:
- `DISPOWEB_NIP` = ton NIP FABER
- `DISPOWEB_BIRTHDAY` = ta date de naissance au format JJ.MM.AAAA

## Etape 1 - calibrage (obligatoire)

Les endpoints dans `dispoweb_bot.py` ont ete deduits en lisant le JavaScript
public du site, mais certains codes (categorie moto exacte, idGroupeExamen,
genre, type d'examen) ne sont visibles qu'une fois connecte. Deux options:

**Option A (recommandee) - via ton navigateur:**
1. Va sur https://ge.ch/tradispoweb_public/ui/app/init/conduite/prive/login
2. Ouvre les outils de developpement (F12) > onglet "Reseau" / "Network"
3. Connecte-toi normalement avec ton NIP + date de naissance
4. Navigue jusqu'a "prendre un rendez-vous" pour l'examen pratique moto
5. Dans la liste des requetes reseau, repere les appels vers `action`,
   `listeLieu`, `listeJourHeureLibre` - clique dessus et regarde l'onglet
   "Payload"/"Charge utile" pour voir les valeurs exactes de
   `idGroupeExamen`, `cat`, `genre`, `type`
6. Reporte ces valeurs dans `.env`
7. Note aussi l'URL exacte de chaque requete si elle differe de celle en
   haut de `dispoweb_bot.py` (variable `BASE`, `ACTION_URL`, etc.) - dis-le
   moi et je corrige le script.

**Option B - mode discover du bot:**
```bash
python dispoweb_bot.py --discover
```
Ca se connecte et affiche les reponses JSON brutes pour t'aider a comprendre
la structure. Utile mais moins complet que l'option A car certains endpoints
ont besoin de parametres qu'on ne connait pas encore au premier essai.

## Etape 2 - tester en mode notification seule

Laisse `DISPOWEB_AUTO_BOOK=false` dans `.env`, puis:

```bash
python dispoweb_bot.py
```

Le bot tourne en boucle, verifie toutes les `DISPOWEB_POLL_INTERVAL` secondes
(240s = 4 min par defaut), et te notifie (bip + notification Windows) des
qu'un nouveau creneau apparait. Tu reserves alors toi-meme en cliquant sur le
lien du site.

## Etape 3 - reservation automatique (optionnel, a tes risques)

Une fois que tu as verifie que la detection fonctionne bien, tu peux passer
`DISPOWEB_AUTO_BOOK=true` dans `.env` pour que le bot reserve lui-meme des
qu'un creneau apparait. Attention:
- Ca engage une reservation reelle, potentiellement difficile a annuler
- Verifie les conditions d'annulation/deplacement du site avant d'activer ca
- Teste d'abord avec `AUTO_BOOK=false` pendant au moins un cycle complet

## Notification WhatsApp (optionnel)

Le bot peut t'envoyer une notification WhatsApp en plus du bip/notification
Windows, via le service gratuit CallMeBot (usage personnel uniquement) :

1. Ajoute **+34 623 91 22 04** a tes contacts WhatsApp
2. Envoie-lui exactement ce message : `I allow callmebot to send me messages`
3. Tu recois une reponse avec ta cle API (`APIKEY`) dans les 2 minutes
4. Ajoute dans `.env` :
   ```
   WHATSAPP_PHONE=+41xxxxxxxxx
   WHATSAPP_APIKEY=1234567
   ```

## Faire tourner le bot sans garder ton PC allume (GitHub Actions)

Le bot peut tourner gratuitement dans le cloud via GitHub Actions, avec une
verification toutes les 5 minutes (minimum impose par GitHub), sans que ton
PC ait besoin d'etre allume.

**1. Cree un compte GitHub** (gratuit) si tu n'en as pas : https://github.com/join

**2. Cree un nouveau depot PUBLIC** (nom libre, ex: `dispoweb-bot`) sur
https://github.com/new - laisse-le vide (pas de README auto-genere).

Un depot public est necessaire pour que les 5 min d'execution restent
gratuites et illimitees. Le *code* sera visible publiquement, mais pas tes
identifiants (voir etape 4, ils restent chiffres et invisibles meme sur un
depot public).

**3. Pousse ce dossier vers ce depot** (remplace `TON-USER/TON-DEPOT`) :
```bash
cd dispoweb-bot
git init
git add dispoweb_bot.py requirements.txt README.md .env.example .gitignore seen_slots.json .github
git commit -m "Bot de surveillance DispoWeb"
git branch -M main
git remote add origin https://github.com/TON-USER/TON-DEPOT.git
git push -u origin main
```
Note : `.env` n'est jamais pousse (il est dans `.gitignore`) - tes
identifiants restent uniquement sur ton PC et dans les Secrets GitHub que tu
vas remplir toi-meme a l'etape suivante.

**4. Ajoute tes identifiants en Secrets GitHub** (chiffres, jamais visibles,
meme pas par moi) : sur la page du depot, va dans
**Settings > Secrets and variables > Actions > New repository secret**, et
cree ces 4 secrets un par un :
- `DISPOWEB_NIP`
- `DISPOWEB_BIRTHDAY`
- `WHATSAPP_PHONE`
- `WHATSAPP_APIKEY`

**5. C'est tout.** Le fichier `.github/workflows/dispoweb.yml` deja present
declenche automatiquement une verification toutes les 5 minutes. Tu peux
aussi la lancer manuellement : onglet **Actions** du depot > "DispoWeb
watch" > **Run workflow**.

Pour changer la categorie/le lieu/l'horizon plus tard, modifie directement
les valeurs dans `.github/workflows/dispoweb.yml` (section `env:`) et pousse
le changement.

### Alternative : garder ton PC (plus simple, mais doit rester allume)

Laisse une fenetre de terminal ouverte avec `python dispoweb_bot.py` actif,
ou utilise le Planificateur de taches Windows (Task Scheduler) pour le
lancer au demarrage. Dis-moi si tu veux de l'aide pour configurer ca.

## A propos du "full" jusqu'en 2027 / fenetre glissante d'ouverture

Ce que tu decris (semaine affichee comme complete au-dela d'un horizon
d'environ 3-4 mois alors qu'en realite les creneaux ne sont simplement pas
encore ouverts) est un pattern classique de systeme de reservation a fenetre
glissante ("rolling release"). Le bot, une fois calibre, vera exactement les
memes donnees que le site affiche - donc si un creneau s'ouvre a heure fixe
(souvent minuit ou tot le matin), le bot le detectera au prochain cycle de
scan apres son ouverture. Si tu identifies un horaire recurrent (ex: tous les
lundis a 00h05), dis-le moi, on peut caler `DISPOWEB_POLL_INTERVAL` et/ou
ajouter un scan plus frequent autour de cet horaire precis.

## Limites et risques a garder en tete
- Site public cantonal: un usage automatise peut techniquement violer les
  conditions d'utilisation du site, meme si ce n'est pas illegal. Garde un
  intervalle de scan raisonnable (le defaut 4 min est deja assez agressif,
  ne descends pas sous 1-2 min) pour ne pas surcharger le serveur ni risquer
  un blocage de ton compte/IP.
- Si le site active un reCAPTCHA sur ce module (actuellement desactive), le
  login automatique cessera de fonctionner - le script remontera une erreur
  claire (HTTP 401/403) plutot que d'essayer de le contourner.
- Les noms exacts de champs dans les reponses `listeJourHeureLibre` ne sont
  pas garantis - si le diff de creneaux ne detecte rien alors qu'il y a des
  changements visibles sur le site, envoie-moi une reponse JSON brute
  (mode `--discover`) pour que j'ajuste le parsing.
