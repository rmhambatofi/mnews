# mnews — agent de veille IA

Agent qui collecte chaque jour l'actualité de l'intelligence artificielle (blogs officiels, médias, Hacker News, Reddit, GitHub, comptes X), demande à Claude de sélectionner et rédiger une newsletter en français, puis l'envoie par email à 9h (heure de Paris).

## Fonctionnement

```
cron 9h ──> collecte des sources ──> sélection (Claude) ──> rédaction (Claude) ──> email SMTP
                 │                        │                      │
                 └── MySQL : items        └── pas de répétition   └── MySQL : newsletters
```

1. **Collecte** (`mnews/pipeline/collect.py`) : chaque source active est interrogée, les nouveaux articles sont stockés dans `items` (dédoublonnés par URL normalisée).
2. **Sélection** (`mnews/pipeline/select.py`) : Claude reçoit les candidats des dernières 36 h et la liste des sujets déjà traités les 10 derniers jours, et renvoie les sujets du jour en JSON structuré.
3. **Rédaction** (`mnews/pipeline/write.py`) : le texte complet des articles principaux est téléchargé, puis Claude rédige la newsletter dans le style de `mnews/prompts/style_example.txt`.
4. **Envoi** (`mnews/mailer.py`) : email texte brut (avec alternative HTML aux liens cliquables) via le SMTP o2switch. En cas d'échec, un email d'alerte est envoyé.

Règles : pas d'envoi le dimanche ni les jours fériés français ; une seule newsletter par jour (sauf `--force`).

## Commandes

```bash
python -m mnews init-db          # tables + sources initiales
python -m mnews check-sources    # teste chaque source sans écrire
python -m mnews collect          # collecte seule
python -m mnews preview          # génère sans envoyer
python -m mnews run              # pipeline complet (commande du cron)
python -m mnews run --force      # ignore dimanche/férié/déjà envoyée
python -m mnews send-test        # email de test SMTP
python -m mnews resend <id>      # renvoie une newsletter existante
python -m mnews web              # interface web en local
```

## Interface web

Flask, servie par Passenger (`passenger_wsgi.py`). Pages : newsletters (lecture, renvoi), sources (ajout, test, activation), articles collectés, journal des exécutions, génération manuelle. Accès par mot de passe (`ADMIN_PASSWORD`). Déclenchement par URL secrète : `/trigger?token=...&mode=send|preview`.

## Installation locale (développement)

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env           # puis remplir
python -m mnews init-db
python -m mnews preview
python -m mnews web
```

Pour tester sans MySQL : `DATABASE_URL=sqlite:///tmp/mnews.db`.

## Déploiement

Voir `deploy/DEPLOY.md` (o2switch : MySQL, Setup Python App, cron).

## Coût

Deux appels Claude par jour (sélection puis rédaction) avec `claude-opus-5-5` : environ 0,20 à 0,40 $ par newsletter, soit moins de 10 $ par mois. Le coût estimé est affiché dans l'interface. Pour réduire : `CLAUDE_MODEL=claude-sonnet-5-5` dans `.env`.
