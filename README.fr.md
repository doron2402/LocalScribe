# LocalScribe

[English](README.md) · [עברית](README.he.md) · [Español](README.es.md) · [简体中文](README.zh-CN.md) · **Français**

Enregistre une réunion sur votre Mac, la transcrit et la résume — le tout sur la
machine. L'audio ne quitte jamais votre poste et, avec le résumeur par défaut,
le texte non plus.

```
mic ─────────┐
             ├─► 16 kHz stereo WAV ─► Whisper ─► transcript ─► local LLM ─► summary.md
system ──────┘   (ch0 = you,          (offline)               (Ollama)
  audio tap       ch1 = them)
```

## Démarrage rapide

```bash
git clone git@github.com:doron2402/LocalScribe.git
cd LocalScribe
./scripts/setup.sh
```

Puis enregistrez :

```bash
localscribe record --label "Standup"
```

Parlez, appuyez sur `Ctrl-C` à la fin de la réunion et patientez quelques
secondes. Vous obtenez trois fichiers :

```
~/LocalScribe/audio/standup_2026-09-01_1000.wav          l'enregistrement
~/LocalScribe/transcripts/standup_2026-09-01_1000.md     qui a dit quoi
~/LocalScribe/summaries/standup_2026-09-01_1000.md       résumé, décisions, actions
```

Trois choses à savoir avant votre première vraie réunion :

- **Lancez `localscribe doctor`.** Il vous dit ce qui manque et comment y remédier.
- **Les deux côtés de l'appel sont captés automatiquement.** Sur macOS 14.4 ou
  plus récent, cela ne demande aucun pilote, aucun mot de passe et aucun
  redémarrage — voir [Audio système](#audio-système-important).
- **Le premier lancement télécharge un modèle vocal d'environ 1,6 Go.**
  `setup.sh` le récupère à l'avance pour que cela n'arrive pas en pleine réunion.

Aucun serveur à démarrer, rien qui tourne en arrière-plan, pas de clé d'API et
pas de réseau.

## Briques open source

| Rôle | Paquet | Licence |
|---|---|---|
| Capture audio | [sounddevice](https://github.com/spatialaudio/python-sounddevice) / PortAudio | MIT |
| Lecture/écriture WAV | [soundfile](https://github.com/bastibe/python-soundfile) / libsndfile | BSD-3 |
| Rééchantillonnage | [soxr](https://github.com/dofuuz/python-soxr) | LGPL-2.1 |
| Reconnaissance vocale | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + CTranslate2 | MIT |
| Reconnaissance vocale (GPU) | [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) | MIT |
| Résumé | [Ollama](https://github.com/ollama/ollama) + Llama 3.1 | MIT / licence Llama |
| Audio système | Core Audio process taps via [pyobjc](https://github.com/ronaldoussoren/pyobjc) | MIT |
| Audio système (macOS ≤ 13) | [BlackHole](https://github.com/ExistentialAudio/BlackHole) | GPL-3.0 |

## Ce que fait `setup.sh`

Il crée l'environnement virtuel, vérifie comment ce Mac peut capter l'audio
système (n'installant BlackHole que si la machine est trop ancienne pour les
taps), télécharge le modèle Whisper, installe Ollama et récupère le modèle de
résumé, ajoute une commande `localscribe` à votre PATH, puis lance les tests et
`doctor`. On peut le relancer sans risque : chaque étape vérifie d'abord.

```bash
./scripts/setup.sh --no-llm                    # sauter Ollama
./scripts/setup.sh --no-audio                  # sauter la vérification audio système
./scripts/setup.sh --whisper small.en          # un modèle vocal plus petit et plus rapide
```

Sur Apple Silicon, le script exige un Python **arm64** : en x86_64, CTranslate2
passe par Rosetta et la transcription prend environ trois fois plus de temps.

### Audio système (important)
<a id="audio-système-important"></a>

Pour enregistrer les personnes à qui vous parlez, LocalScribe doit capter ce qui
*sort* de vos haut-parleurs, pas seulement ce qui entre dans votre micro.

Sur **macOS 14.4 et plus récent**, il le fait avec un *process tap* Core Audio :
l'enregistreur se branche directement sur la sortie du système et l'enveloppe
dans un périphérique d'entrée temporaire qui n'existe que pendant
l'enregistrement. Aucun pilote, aucun mot de passe administrateur, aucun
redémarrage, et rien qui subsiste ensuite. `localscribe doctor` vous le confirme.

Au premier enregistrement, macOS peut demander l'autorisation dans
**Confidentialité et sécurité → Enregistrement de l'écran et de l'audio
système**. Accordez-la une fois.

Sur **macOS 13 ou antérieur**, les taps n'existent pas et il faut un pilote de
boucle. `setup.sh` installe [BlackHole](https://existential.audio/blackhole/)
(mot de passe et redémarrage requis), puis :

1. Ouvrez **Audio MIDI Setup** (dans `/Applications/Utilities`).
2. `+` → **Create Multi-Output Device**, cochez votre casque ou vos enceintes
   **et** BlackHole 2ch.
3. Définissez ce Multi-Output Device comme sortie audio du Mac.

Dans les deux cas, vous entendez toujours l'appel normalement.

```bash
localscribe record --system-audio tap      # imposer le tap Core Audio
localscribe record --system-audio device   # imposer BlackHole ou équivalent
localscribe record --system-audio off      # micro seulement
```

## Faut-il un serveur ?

Non. LocalScribe est une commande ponctuelle : elle enregistre, transcrit,
résume, écrit deux fichiers markdown et se termine. Rien n'écoute sur un port,
rien ne tourne entre deux réunions, et rien n'a besoin du réseau.

Seule exception : le résumeur local. Ollama est un service sur
`127.0.0.1:11434`, et LocalScribe le démarre à la demande s'il n'est pas déjà
actif — ce n'est donc pas non plus à vous d'y penser. Mettez
`LOCALSCRIBE_OLLAMA_AUTOSTART=0` pour le gérer vous-même, ou utilisez
`--backend extractive` et il n'y a plus aucun service.

## Utilisation

`scripts/setup.sh` ajoute une commande `localscribe` à votre PATH. Sinon, lancez
`./bin/localscribe` depuis le dépôt — même chose, sans environnement à activer.

```bash
localscribe doctor                       # vérifier l'installation
localscribe devices                      # lister les périphériques d'entrée

# Enregistrer jusqu'à Ctrl-C, puis transcrire et résumer
localscribe record --label "Latency sync"

# Arrêt automatique après une durée fixée
localscribe record --label "Standup" --duration 20m

# Retraiter un enregistrement existant (après avoir changé le prompt ou le modèle)
localscribe process ~/LocalScribe/audio/standup_2026-08-31_1000.wav

# Refaire le résumé sans retranscrire
localscribe summarize ~/LocalScribe/transcripts/standup_2026-08-31_1000.json

localscribe list                         # ce que vous avez enregistré jusqu'ici
```

Les fichiers sont écrits dans `~/LocalScribe/{audio,transcripts,summaries}`.

## Qui a dit quoi

Les deux sources audio sont conservées sur des canaux distincts — votre micro à
gauche, l'audio système à droite — si bien que LocalScribe identifie les
locuteurs en comparant l'énergie des canaux, mot par mot, au lieu d'exécuter un
modèle de diarisation. Vous obtenez **You** et **Them**, pas de vrais noms, mais
c'est gratuit et sans modèle Hugging Face à accès restreint. Un `?` final
(`Them?`) signale un mot où les deux côtés parlaient en même temps.

L'astuce tient à une asymétrie : le canal système ne peut contenir que le
correspondant, jamais vous. Un audio système fort signifie donc que c'est *lui*
qui parle, et la seule question restante est de savoir si vous parlez aussi — ce
à quoi répond le fait que votre micro porte ou non plus de signal que la fuite
des haut-parleurs ne peut l'expliquer.

Votre micro entend toujours un peu vos haut-parleurs, et lui soustraire le canal
système n'y change rien : le temps que le son traverse la pièce, il a été étalé
par l'écho, si bien qu'une copie retardée n'annule presque rien (mesuré sur un
enregistrement réel : 0,1 dB). LocalScribe compare plutôt des enveloppes
d'énergie lissées à un taux de fuite mesuré, ce qui résiste à la réverbération.

Pour de vrais noms, passez la transcription au résumeur : un modèle de langue les
déduit généralement de la façon dont les gens s'adressent les uns aux autres.

## Moteurs

Deux moteurs vocaux, une seule sortie. `auto` (la valeur par défaut) choisit
`mlx` quand il est installé sur Apple Silicon, et `faster-whisper` partout
ailleurs.

```bash
localscribe process recording.wav --engine mlx             # GPU Metal
localscribe process recording.wav --engine faster-whisper  # CPU, partout
```

262 secondes d'audio, `base.en`, chaîne complète y compris l'attribution du
locuteur mot par mot, sur un Mac à puce série M :

| Moteur | Calcul | Durée |
|---|---|---|
| faster-whisper (CTranslate2) | CPU int8 | 11,9 s |
| **mlx-whisper** | **GPU Metal** | **4,2 s** |

Le premier lancement de `mlx` sur une installation neuve ajoute environ 30 s, le
temps que Metal compile ses noyaux. C'est ponctuel et mis en cache ;
`scripts/setup.sh` paie ce coût pour vous afin qu'il ne tombe pas sur votre
première réunion.

Bon à savoir si vous envisagez une réécriture dans un langage compilé : ce n'est
pas le langage hôte qui fait la vitesse. Sur la même machine et le même audio,
whisper.cpp — le moteur C++ auquel un portage Go ou Rust se lierait via cgo —
prend **10,2 s sur le CPU** et **2,2 s sur le GPU**, contre 5,7 s pour
CTranslate2 et 2,9 s pour MLX à travail égal. Le Python qui orchestre tout cela
coûte moins de 0,1 s sur les 11,9 s ci-dessus : une exécution complète et son
simple appel de reconnaissance vocale donnent le même chiffre, au bruit près.
Le levier, c'est le GPU, pas le langage.

## Choisir un modèle Whisper

| Modèle | Taille | Vitesse (série M, int8) | Quand |
|---|---|---|---|
| `base.en` | 140 Mo | ~15x le temps réel | vérifications rapides, audio propre |
| `small.en` | 460 Mo | ~8x le temps réel | correct, anglais seulement |
| `medium.en` | 1,5 Go | ~3x le temps réel | bon |
| `large-v3-turbo` | 1,6 Go | ~4x le temps réel | **par défaut** — le meilleur avec les accents |

```bash
localscribe process recording.wav --model small.en
```

Ces vitesses concernent le moteur CPU ; le moteur `mlx` est environ trois fois
plus rapide que chacune. Une réunion d'une heure avec `large-v3-turbo`, c'est
environ 15 minutes sur le CPU et environ 5 sur le GPU.

## Résumeurs

- `--backend ollama` (par défaut) — un modèle local. Entièrement hors ligne.
- `--backend anthropic` — l'API Claude. Seul le **texte** de la transcription est
  envoyé, jamais l'audio. Nécessite `ANTHROPIC_API_KEY`.
- `--backend extractive` — aucun modèle : des phrases classées par mots-clés et
  des actions repérées par expressions régulières. Grossier, mais instantané et
  sans dépendance. C'est aussi le repli automatique quand le moteur choisi est
  injoignable.

Les transcriptions de plus de ~1800 mots sont résumées façon map-reduce : chaque
fragment est lu séparément, puis les notes sont fusionnées.

## Développement

```bash
.venv/bin/pytest          # 60 tests, sans matériel audio ni téléchargement de modèle
.venv/bin/ruff check .
```

Les tests couvrent ce qui échoue en silence plutôt que bruyamment : l'attribution
du locuteur (découpage des tours de parole, fuite des haut-parleurs vers le
micro, écarts de gain, paroles simultanées), la dérive d'horloge de
l'enregistreur, la sélection du moteur et ses replis, et le post-traitement du
résumé.

```
localscribe/
├── localscribe/
│   ├── audio.py        capture, rééchantillonnage, alignement de deux horloges
│   ├── engines.py      faster-whisper (CPU) et mlx (GPU Metal), une seule forme
│   ├── transcribe.py   reconnaissance vocale + attribution du locuteur par mot
│   ├── summarize.py    backends ollama / anthropic / extractive
│   ├── config.py       variables d'environnement, toutes optionnelles
│   └── cli.py          record, process, summarize, devices, doctor, list
├── bin/localscribe     lanceur : exécute le CLI de l'environnement depuis partout
├── scripts/setup.sh    installation en une commande
└── tests/
```

## Avant d'enregistrer d'autres personnes

Un enregistrement capte tout le monde sur l'appel. Les règles de consentement
varient selon les juridictions et certaines exigent l'accord de toutes les
parties — prévenez la salle que l'enregistrement tourne.
