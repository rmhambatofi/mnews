# Déploiement sur o2switch

Prérequis : accès cPanel + SSH, une boîte mail `no-reply@manitra.fr`, une clé API Anthropic.

## 1. Base de données MySQL

Dans cPanel > **Bases de données MySQL** :

1. Créer une base, par exemple `USER_mnews`.
2. Créer un utilisateur, par exemple `USER_mnews`, avec un mot de passe fort.
3. Ajouter l'utilisateur à la base avec **tous les privilèges**.

L'URL de connexion sera : `mysql+pymysql://USER_mnews:MOTDEPASSE@localhost/USER_mnews?charset=utf8mb4`

## 2. Boîte mail d'envoi

Dans cPanel > **Comptes de messagerie**, créer `no-reply@manitra.fr` si elle n'existe pas, et noter son mot de passe.
Le serveur SMTP est généralement `mail.manitra.fr` (port 465, SSL). En cas de doute, cPanel > Comptes de messagerie > **Connecter des appareils** affiche les paramètres exacts.

## 3. Envoyer le code

Depuis votre machine (PowerShell), en remplaçant `USER` et `serveur` :

```powershell
scp -r C:\Projects\manitra\mnews USER@serveur.o2switch.net:~/mnews
```

ou via Git si le projet est sur un dépôt :

```bash
ssh USER@serveur.o2switch.net
git clone <url-du-depot> ~/mnews
```

## 4. Application Python (cPanel > Setup Python App)

1. **Create Application** :
   - Python version : **3.11** (minimum 3.10, exigé par le SDK Anthropic)
   - Application root : `mnews`
   - Application URL : le sous-domaine ou le dossier voulu, par exemple `news.manitra.fr`
   - Application startup file : `passenger_wsgi.py`
   - Application entry point : `application`
2. Cliquer sur **Create**, puis copier la commande affichée en haut (« Enter to the virtual environment »), de la forme :
   `source /home/USER/virtualenv/mnews/3.11/bin/activate && cd /home/USER/mnews`
3. En SSH, activer le venv avec cette commande puis installer les dépendances :

```bash
pip install -r requirements.txt
```

## 5. Configuration

```bash
cd ~/mnews
cp .env.example .env
nano .env
```

Remplir au minimum : `DATABASE_URL`, `ANTHROPIC_API_KEY`, `SMTP_PASSWORD`, `ADMIN_PASSWORD`, `SECRET_KEY`, `TRIGGER_TOKEN`.
Pour générer `SECRET_KEY` et `TRIGGER_TOKEN` :

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Puis, toujours dans le venv :

```bash
python -m mnews init-db        # crée les tables et les sources initiales
python -m mnews send-test      # vérifie le SMTP : vous devez recevoir un email
python -m mnews check-sources  # vérifie que les sources répondent
python -m mnews preview        # génère une newsletter sans l'envoyer (appelle Claude)
```

Si l'aperçu vous convient : `python -m mnews run --force` envoie la newsletter du jour.

## 6. Interface web

Dans cPanel > Setup Python App, cliquer sur **Restart** après chaque modification du code ou du `.env`.
L'interface est alors disponible sur l'URL choisie, protégée par `ADMIN_PASSWORD`.

Activez le HTTPS (Let's Encrypt est automatique chez o2switch) avant d'utiliser l'interface.

## 7. Tâche cron (envoi quotidien à 9h)

cPanel > **Tâches cron** > ajouter la ligne de `deploy/crontab.txt` en adaptant `USER` et le chemin du venv.

Le script :
- ne fait rien le dimanche et les jours fériés français,
- ne renvoie pas une newsletter déjà envoyée le même jour,
- envoie un email d'alerte à `ALERT_TO` en cas d'échec.

## 8. Déclenchement manuel

- Depuis l'interface web : bouton « Aperçu sans envoi » ou « Générer et envoyer ».
- Par URL (pour un raccourci ou un autre cron) : `https://news.manitra.fr/trigger?token=TRIGGER_TOKEN&mode=send`
  (`mode=preview` pour un aperçu).
- En SSH : `python -m mnews run --force` ou `python -m mnews preview`.

## 9. Mise à jour du code

```bash
cd ~/mnews && git pull          # ou nouveau scp
source /home/USER/virtualenv/mnews/3.11/bin/activate
pip install -r requirements.txt
python -m mnews init-db --no-seed   # crée les nouvelles tables éventuelles
```

puis **Restart** dans Setup Python App.

## Dépannage

- `Variables manquantes dans .env` : une variable obligatoire est vide.
- `Plafond mensuel atteint` : la dépense Claude du mois a atteint `MONTHLY_BUDGET_EUR`. Plus aucune génération jusqu'au 1er du mois suivant, sauf si vous relevez le plafond dans `.env` (puis Restart).
- Erreur SMTP `535` : mauvais mot de passe de la boîte `no-reply@`.
- Les comptes X renvoient des erreurs : la passerelle Nitter est hors ligne. Changer `X_RSS_TEMPLATE` dans `.env` pour une autre instance (par exemple `https://nitter.privacydev.net/{handle}/rss`) ou désactiver ces sources dans l'interface.
- Journaux : `logs/mnews.log`, `logs/cron.log`, et la page **Exécutions** de l'interface.
