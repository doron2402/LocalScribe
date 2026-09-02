# LocalScribe

[English](README.md) · [עברית](README.he.md) · **Español** · [简体中文](README.zh-CN.md) · [Français](README.fr.md)

Graba una reunión en tu Mac, la transcribe y la resume — todo en la propia
máquina. El audio nunca sale de tu equipo y, con el resumidor por defecto,
el texto tampoco.

```
mic ─────────┐
             ├─► 16 kHz stereo WAV ─► Whisper ─► transcript ─► local LLM ─► summary.md
system ──────┘   (ch0 = you,          (offline)               (Ollama)
  audio tap       ch1 = them)
```

## Inicio rápido

```bash
git clone git@github.com:doron2402/LocalScribe.git
cd LocalScribe
./scripts/setup.sh
```

Y a grabar:

```bash
localscribe record --label "Standup"
```

Habla, pulsa `Ctrl-C` cuando termine la reunión y espera unos segundos.
Obtienes tres archivos:

```
~/LocalScribe/audio/standup_2026-09-01_1000.wav          la grabación
~/LocalScribe/transcripts/standup_2026-09-01_1000.md     quién dijo qué
~/LocalScribe/summaries/standup_2026-09-01_1000.md       resumen, decisiones, tareas
```

Tres cosas que conviene saber antes de tu primera reunión de verdad:

- **Ejecuta `localscribe doctor`.** Te dice qué falta y cómo arreglarlo.
- **Ambos lados de la llamada se capturan automáticamente.** En macOS 14.4 o
  posterior no hace falta ningún controlador, ni contraseña, ni reiniciar —
  consulta [Audio del sistema](#audio-del-sistema-importante).
- **La primera ejecución descarga un modelo de voz de ~1,6 GB.** `setup.sh` lo
  baja de antemano para que no ocurra en mitad de una reunión.

No hay servidor que arrancar, nada corriendo en segundo plano, ni clave de API
ni red.

## Componentes de código abierto

| Función | Paquete | Licencia |
|---|---|---|
| Captura de audio | [sounddevice](https://github.com/spatialaudio/python-sounddevice) / PortAudio | MIT |
| Lectura/escritura WAV | [soundfile](https://github.com/bastibe/python-soundfile) / libsndfile | BSD-3 |
| Remuestreo | [soxr](https://github.com/dofuuz/python-soxr) | LGPL-2.1 |
| Voz a texto | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + CTranslate2 | MIT |
| Voz a texto (GPU) | [mlx-whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) | MIT |
| Resumen | [Ollama](https://github.com/ollama/ollama) + Llama 3.1 | MIT / licencia Llama |
| Audio del sistema | Core Audio process taps vía [pyobjc](https://github.com/ronaldoussoren/pyobjc) | MIT |
| Audio del sistema (macOS ≤ 13) | [BlackHole](https://github.com/ExistentialAudio/BlackHole) | GPL-3.0 |

## Qué hace `setup.sh`

Crea el entorno virtual, comprueba cómo puede capturar audio del sistema este
Mac (instalando BlackHole solo si la máquina es demasiado antigua para los
taps), descarga el modelo de Whisper, instala Ollama y descarga el modelo de
resumen, enlaza el comando `localscribe` en tu PATH y luego ejecuta las pruebas
y `doctor`. Se puede volver a ejecutar sin riesgo: cada paso comprueba antes.

```bash
./scripts/setup.sh --no-llm                    # omitir Ollama por completo
./scripts/setup.sh --no-audio                  # omitir la comprobación de audio del sistema
./scripts/setup.sh --whisper small.en          # un modelo de voz más pequeño y rápido
```

En Apple Silicon el script exige un Python **arm64**: uno de x86_64 ejecuta
CTranslate2 bajo Rosetta y la transcripción tarda unas tres veces más.

### Audio del sistema (importante)
<a id="audio-del-sistema-importante"></a>

Para grabar a las personas con las que hablas, LocalScribe tiene que capturar lo
que sale por tus altavoces, no solo lo que entra por el micrófono.

En **macOS 14.4 y posterior** lo hace con un *process tap* de Core Audio: la
grabación se engancha directamente a la salida del sistema y la envuelve en un
dispositivo de entrada temporal que solo existe mientras grabas. Sin controlador,
sin contraseña de administrador, sin reiniciar y sin dejar nada detrás.
`localscribe doctor` te lo confirma.

La primera vez que grabes, macOS puede pedir permiso en **Privacidad y seguridad
→ Grabación de pantalla y audio del sistema**. Concédelo una vez.

En **macOS 13 o anterior** no existen los taps y hace falta un controlador de
bucle. `setup.sh` instala [BlackHole](https://existential.audio/blackhole/)
(pide tu contraseña y un reinicio), y después:

1. Abre **Audio MIDI Setup** (en `/Applications/Utilities`).
2. `+` → **Create Multi-Output Device**, y marca tus auriculares o altavoces
   **y** BlackHole 2ch.
3. Selecciona ese Multi-Output Device como salida de sonido del Mac.

En ambos casos sigues oyendo la llamada con normalidad.

```bash
localscribe record --system-audio tap      # exigir el tap de Core Audio
localscribe record --system-audio device   # exigir BlackHole o similar
localscribe record --system-audio off      # solo micrófono
```

## ¿Necesita un servidor?

No. LocalScribe es un comando de una sola pasada: graba, transcribe, resume,
escribe dos archivos markdown y termina. Nada escucha en un puerto, nada queda
corriendo entre reuniones y nada necesita la red.

La única excepción es el resumidor local. Ollama es un servicio en
`127.0.0.1:11434`, y LocalScribe lo arranca cuando hace falta si no está activo,
así que tampoco es algo que tengas que recordar. Usa
`LOCALSCRIBE_OLLAMA_AUTOSTART=0` para gestionarlo tú, o `--backend extractive`
y entonces no hay ningún servicio.

## Uso

`scripts/setup.sh` enlaza un comando `localscribe` en tu PATH. Si no, ejecuta
`./bin/localscribe` desde el repositorio: lo mismo, sin activar ningún entorno.

```bash
localscribe doctor                       # comprobar la instalación
localscribe devices                      # listar dispositivos de entrada

# Grabar hasta Ctrl-C, luego transcribir y resumir
localscribe record --label "Latency sync"

# Parada automática tras un tiempo fijo
localscribe record --label "Standup" --duration 20m

# Reprocesar una grabación existente (p. ej. tras cambiar el prompt o el modelo)
localscribe process ~/LocalScribe/audio/standup_2026-08-31_1000.wav

# Volver a resumir una transcripción sin transcribir de nuevo
localscribe summarize ~/LocalScribe/transcripts/standup_2026-08-31_1000.json

localscribe list                         # qué has grabado hasta ahora
```

La salida se escribe en `~/LocalScribe/{audio,transcripts,summaries}`.

## Conservar y borrar

Las grabaciones se borran a los **30 días**. Las transcripciones y los resúmenes
hechos a partir de ellas se conservan: son diminutos y son la razón por la que
grabaste. El audio es lo que se acumula (unos 60 MB por hora) y lo que contiene
las voces de otras personas, así que es la parte con reloj.

Los archivos viejos se barren al empezar cada `record` y cada `process`, de modo
que la política se aplica sin ningún cron. También puedes hacerlo a mano:

```bash
localscribe prune --dry-run              # show what would go, delete nothing
localscribe prune                        # delete it
localscribe prune --all --days 90        # expire the notes too, after 90 days
```

Cambia los valores por defecto en `.env`:

```bash
LOCALSCRIBE_RETENTION_DAYS=7             # audio; 0 keeps it forever
LOCALSCRIBE_RETENTION_TRANSCRIPTS=90     # 0 by default, meaning keep
LOCALSCRIBE_RETENTION_SUMMARIES=0
```

El borrado es permanente: no hay papelera ni deshacer. Es deliberadamente
estrecho en lo que toca: solo dentro de los directorios de LocalScribe, solo los
tipos de archivo que él escribe y nunca a través de un enlace simbólico, así que
un archivo ajeno que dejes ahí está a salvo.

## Quién dijo qué

Las dos fuentes de audio se guardan en canales separados — tu micrófono en el
izquierdo y el audio del sistema en el derecho — así que LocalScribe etiqueta a
los hablantes comparando la energía de cada canal, palabra por palabra, en lugar
de ejecutar un modelo de diarización. Eso te da **You** frente a **Them**, no
nombres propios, pero es gratuito y no necesita ningún modelo restringido de
Hugging Face. Una `?` final (`Them?`) marca una palabra en la que ambos lados
hablaban a la vez.

La clave es una asimetría: el canal del sistema solo puede contener al otro
extremo, nunca a ti. Así que un audio del sistema fuerte significa que hablan
*ellos*, y la única pregunta que queda es si tú hablas también — algo que
responde si tu micrófono lleva más señal de la que la filtración de los
altavoces puede explicar.

Tu micrófono siempre oye un poco tus altavoces, y restarle el canal del sistema
no lo elimina: cuando el sonido ha cruzado la sala ya viene emborronado por el
eco, así que una copia retrasada no cancela casi nada (medido en una grabación
real: 0,1 dB). LocalScribe compara envolventes de energía suavizadas contra una
proporción de filtración medida, que sí sobrevive a la reverberación.

Para obtener nombres reales, pasa la transcripción por el resumidor: un modelo de
lenguaje suele deducirlos por cómo se dirigen las personas entre sí.

## Motores

Dos motores de voz, la misma salida. `auto` (el valor por defecto) elige `mlx`
cuando está instalado en Apple Silicon, y `faster-whisper` en cualquier otro caso.

```bash
localscribe process recording.wav --engine mlx             # GPU Metal
localscribe process recording.wav --engine faster-whisper  # CPU, funciona en todas partes
```

262 segundos de audio, `base.en`, proceso completo incluida la atribución de
hablante palabra por palabra, en un Mac con chip de la serie M:

| Motor | Cómputo | Tiempo |
|---|---|---|
| faster-whisper (CTranslate2) | CPU int8 | 11,9 s |
| **mlx-whisper** | **GPU Metal** | **4,2 s** |

La primera ejecución de `mlx` en una instalación nueva añade unos 30 s mientras
Metal compila sus kernels. Es un coste único y se cachea; `scripts/setup.sh` lo
paga por ti para que no te toque en tu primera reunión.

Conviene saberlo si te planteas reescribirlo en un lenguaje compilado: no es el
lenguaje anfitrión lo que da la velocidad. En la misma máquina y el mismo audio,
whisper.cpp —el motor C++ al que un port en Go o Rust se enlazaría vía cgo—
tarda **10,2 s en la CPU** y **2,2 s en la GPU**, frente a los 5,7 s de
CTranslate2 y los 2,9 s de MLX para el mismo trabajo. El Python que orquesta
todo esto cuesta menos de 0,1 s de los 11,9 s de arriba: una ejecución completa
y su llamada de voz a texto desnuda dan el mismo número dentro del margen de
ruido. La palanca es la GPU, no el lenguaje.

## Elegir un modelo de Whisper

| Modelo | Tamaño | Velocidad (serie M, int8) | Cuándo |
|---|---|---|---|
| `base.en` | 140 MB | ~15x tiempo real | comprobaciones rápidas, audio limpio |
| `small.en` | 460 MB | ~8x tiempo real | aceptable, solo inglés |
| `medium.en` | 1,5 GB | ~3x tiempo real | bueno |
| `large-v3-turbo` | 1,6 GB | ~4x tiempo real | **por defecto** — el mejor con acentos |

```bash
localscribe process recording.wav --model small.en
```

Las velocidades son para el motor de CPU; el motor `mlx` es unas tres veces más
rápido que cada una. Una reunión de una hora con `large-v3-turbo` son unos 15
minutos en la CPU y unos 5 en la GPU.

## Resumidores

- `--backend ollama` (por defecto) — un modelo local. Totalmente sin conexión.
- `--backend anthropic` — la API de Claude. Solo se envía el **texto** de la
  transcripción, nunca el audio. Requiere `ANTHROPIC_API_KEY`.
- `--backend extractive` — sin modelo alguno: frases ordenadas por palabras clave
  y tareas detectadas con expresiones regulares. Tosco, pero inmediato y sin
  dependencias. Es también el recurso automático cuando el motor elegido no
  responde.

Las transcripciones de más de ~1800 palabras se resumen al estilo map-reduce:
cada fragmento se lee por separado y luego se fusionan las notas.

## Desarrollo

```bash
.venv/bin/pytest          # 60 pruebas, sin hardware de audio ni descargas de modelos
.venv/bin/ruff check .
```

Las pruebas cubren las partes que fallan en silencio y no a gritos: atribución de
hablante (división de turnos, filtración de los altavoces al micrófono,
diferencias de ganancia, voces solapadas), la deriva de reloj en el grabador, la
selección de motor y sus alternativas, y el postprocesado del resumen.

```
localscribe/
├── localscribe/
│   ├── audio.py        captura, remuestreo, alineación de dos relojes
│   ├── engines.py      faster-whisper (CPU) y mlx (GPU Metal), una sola forma
│   ├── transcribe.py   voz a texto + atribución de hablante por palabra
│   ├── summarize.py    motores ollama / anthropic / extractive
│   ├── config.py       variables de entorno, todas opcionales
│   └── cli.py          record, process, summarize, devices, doctor, list
├── bin/localscribe     lanzador: ejecuta el CLI del entorno desde cualquier sitio
├── scripts/setup.sh    instalación en un solo comando
└── tests/
```

## Antes de grabar a otras personas

Una grabación captura a todos los presentes en la llamada. Las normas de
consentimiento varían según la jurisdicción y en algunas hace falta el
consentimiento de todas las partes: avisa a la sala de que la grabación está en
marcha.
