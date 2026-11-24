# tweny_finds

Bot de Telegram privado: le escribes un nombre o le mandas una foto y te devuelve artículos
(con sus enlaces) de los canales públicos que hay en `channels.txt`.

- Lee la vista pública de los canales (`t.me/s/<canal>`), sin usar tu cuenta de Telegram.
- Busca por texto (marcas, nombres) y por foto (parecido visual, CLIP en versión ONNX).
- Un solo proceso: el bot contesta y, en segundo plano, relee los canales cada 30 minutos.

## Variables de entorno (en el servidor)

| Variable | Obligatoria | Qué es |
|---|---|---|
| `BOT_TOKEN` | sí | El token que te dio @BotFather |
| `ALLOWED_USERS` | sí (tras el primer arranque) | Tu id de Telegram. Si está vacía, el bot te dice cuál es cuando le escribes |
| `DATA_DIR` | ya viene en el Dockerfile (`/data`) | Carpeta donde se guardan base de datos, fotos y modelo. **Debe ser un volumen persistente** |
| `CHANNELS` | no | Canales separados por comas. Si no existe, se usa `channels.txt` |
| `SCRAPE_INTERVAL_MIN` | no | Minutos entre lecturas (por defecto 30) |

## Despliegue en Railway (todo desde el navegador)

1. Entra en railway.com con tu cuenta de GitHub y crea un proyecto nuevo desde el repositorio `tweny_finds`.
2. En el servicio, pestaña **Variables**: añade `BOT_TOKEN` (y `ALLOWED_USERS` cuando sepas tu id).
3. Añade un **Volume** (volumen) al servicio y móntalo en la ruta `/data`. Sin esto, cada reinicio borra los datos.
4. Mira los **Logs**: debe poner "Bot en marcha" y luego ir leyendo los canales.
5. En Telegram escribe `/start` al bot: te dirá tu id. Ponlo en `ALLOWED_USERS` y deja que se reinicie.
6. Escribe `/estado`: te dice cuántos artículos y fotos tiene y si algún canal da error.

La primera lectura baja el historial reciente de cada canal y el modelo de fotos (unos 350 MB), así que
las búsquedas por foto tardan en funcionar los primeros minutos. `/estado` muestra cuántas fotos están listas.

## Probar en local (opcional)

```
pip install -r requirements.txt
python tests/test_core.py
BOT_TOKEN=... python bot.py
```

## Limitaciones conocidas

- Solo canales públicos con vista previa web. Si un canal da error en `/estado`, es posible que no la tenga.
- Los álbumes de varias fotos pueden aparecer como una sola publicación o como varias, según cómo los muestre Telegram.
- Aún no convierte enlaces a Usfans ni comprueba el QC (fases 2 y 3).
- Los embeddings se cargan en memoria para buscar: con decenas de miles de fotos habrá que optimizarlo.
