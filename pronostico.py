# Pronóstico para Radar Mendoza: baja la última corrida del WRF del SMN (lluvia, temperatura y viento) y del ICON del
# DWD (nubes), recorta el cuadro del radar, lo pasa a Mercator y guarda un PNG gris de 8 bits por capa y hora, más
# meta.json con las corridas, las horas y cómo pasar cada byte a su unidad (valor = desde + paso × byte).
#
#   python pronostico.py ver URL_DEL_META_PUBLICADO [--forzar]   → renglones clave=valor para GITHUB_OUTPUT
#   python pronostico.py armar SALIDA --wrf AAAAMMDDHH --icon AAAAMMDDHH [--plazos 0,1,24]
#   python pronostico.py medir acierto.csv   → suma a la planilla las horas terminadas que falten (ver «medir»)
#
# «ver» usa solo la biblioteca estándar (corre antes de instalar nada): si las corridas son las ya publicadas, no se arma.
import datetime as dt
import json
import math
import sys
import time
import urllib.error
import urllib.request

WRF = "https://smn-ar-wrf.s3.amazonaws.com/DATA/WRF/DET"
ICON = "https://opendata.dwd.de/weather/nwp/icon/grib"
PLAZOS = range(0, 73)  # hora por hora, hasta 72 h
VIEJO_H = 18  # una corrida con más horas que esto ya no se muestra (veredicto del council)

# El cuadro del radar, como src/radar/cuadros.ts, y su caja en Mercator.
LAT, LON, KLAT, KLON, AJ = -34, -68, 110.924, 92.425, 0.046
SUR, OESTE = LAT - 250 / KLAT + AJ, LON - 250 / KLON
NORTE, ESTE = LAT + 450 / KLAT + AJ, LON + 250 / KLON
R = 6378137
ANCHO = 250  # 2 km por píxel: cada celda de 4 km del WRF queda en 2×2 sin perder nada.


def my(lat):
    return R * math.log(math.tan(math.pi / 4 + lat * math.pi / 360))


ALTO = round(ANCHO * (my(NORTE) - my(SUR)) / (math.radians(ESTE - OESTE) * R))

# Cómo leer cada byte: valor = desde + paso × byte. 255 es el tope (lo que pasa, queda en 255).
CAPAS = {
    "lluvia": {"desde": 0, "paso": 0.1, "unidad": "mm en la hora"},
    "temp": {"desde": -40, "paso": 0.5, "unidad": "°C a 2 m"},
    "viento": {"desde": 0, "paso": 1, "unidad": "km/h a 10 m"},
    "rumbo": {"desde": 0, "paso": 2, "unidad": "grados, de dónde viene"},
    "nubes": {"desde": 0, "paso": 1, "unidad": "% del cielo cubierto"},
}


def pedir(url, tiempo=60, intentos=3, cabeza=False):
    for n in range(intentos):
        try:
            req = urllib.request.Request(url, method="HEAD" if cabeza else "GET",
                                         headers={"User-Agent": "radar-mendoza-pronostico"})
            with urllib.request.urlopen(req, timeout=tiempo) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if n == intentos - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if n == intentos - 1:
                raise
        time.sleep(5 * (n + 1))


def nombre(t):
    return t.strftime("%Y%m%d%H")


def de_nombre(s):
    return dt.datetime.strptime(s, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)


def url_wrf(corrida, plazo):
    c = corrida
    return f"{WRF}/{c:%Y/%m/%d/%H}/WRFDETAR_01H_{c:%Y%m%d_%H}_{plazo:03d}.nc"


def url_icon(corrida, cosa):
    # cosa: un plazo (int) para la cobertura total, o "CLAT"/"CLON" para las coordenadas de la grilla.
    if isinstance(cosa, int):
        return f"{ICON}/{corrida:%H}/clct/icon_global_icosahedral_single-level_{nombre(corrida)}_{cosa:03d}_CLCT.grib2.bz2"
    return f"{ICON}/{corrida:%H}/{cosa.lower()}/icon_global_icosahedral_time-invariant_{nombre(corrida)}_{cosa}.grib2.bz2"


def ultima(completa, ahora):
    # La última corrida (00, 06, 12, 18 UTC) que ya tiene la hora 72 subida; mira hasta un día atrás.
    base = ahora.replace(hour=ahora.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for k in range(5):
        c = base - dt.timedelta(hours=6 * k)
        if pedir(completa(c), tiempo=20, cabeza=True) is not None:
            return c
    return None


def ver(url_meta, forzar):
    ahora = dt.datetime.now(dt.timezone.utc)
    wrf = ultima(lambda c: url_wrf(c, PLAZOS[-1]), ahora)
    icon = ultima(lambda c: url_icon(c, PLAZOS[-1]), ahora)
    try:
        crudo = pedir(url_meta, tiempo=20)
        antes = json.loads(crudo) if crudo else {}
    except (ValueError, OSError):
        antes = {}
    ya = ((antes.get("wrf") or {}).get("corrida"), (antes.get("icon") or {}).get("corrida"))
    nuevas = (wrf and nombre(wrf), icon and nombre(icon))
    hay = any(nuevas)
    cambio = hay and (forzar or nuevas != ya)
    viejo = wrf is None or (ahora - wrf).total_seconds() > VIEJO_H * 3600
    print(f"wrf={nuevas[0] or ''}")
    print(f"icon={nuevas[1] or ''}")
    print(f"cambio={int(bool(cambio))}")
    print(f"viejo={int(viejo)}")
    print(f"publicado: wrf {ya[0]}, icon {ya[1]}; últimas: wrf {nuevas[0]}, icon {nuevas[1]}", file=sys.stderr)


# --- armar (necesita requirements.txt) ---

def _grilla_mercator():
    import numpy as np

    x0, x1 = R * math.radians(OESTE), R * math.radians(ESTE)
    y0, y1 = my(SUR), my(NORTE)
    xs = x0 + (np.arange(ANCHO) + 0.5) * (x1 - x0) / ANCHO
    ys = y1 - (np.arange(ALTO) + 0.5) * (y1 - y0) / ALTO
    return np.meshgrid(xs, ys)


_LCC = None


def leer_wrf(args):
    # Un plazo del WRF: lee por rango solo las cuatro variables (cada una es un bloque zlib del campo entero) y
    # devuelve el recorte en Mercator, vecino más cercano.
    corrida, plazo = args
    global _LCC
    import fsspec
    import h5py
    import numpy as np
    from pyproj import Transformer

    if _LCC is None:
        lcc = "+proj=lcc +lat_1=-35 +lat_2=-35 +lat_0=-34.99999 +lon_0=-65 +a=6370000 +b=6370000 +units=m +no_defs"
        MX, MY = _grilla_mercator()
        _LCC = Transformer.from_crs("EPSG:3857", lcc, always_xy=True).transform(MX, MY)
    LX, LY = _LCC
    fs = fsspec.filesystem("http", block_size=2**20, cache_type="none")
    for n in range(3):
        try:
            with fs.open(url_wrf(corrida, plazo), "rb", block_size=2**20, cache_type="bytes") as f, h5py.File(f, "r") as h:
                x, y = h["x"][:], h["y"][:]
                ix = np.rint((LX - x[0]) / (x[1] - x[0])).astype(int)
                iy = np.rint((LY - y[0]) / (y[1] - y[0])).astype(int)
                return plazo, {k: h[k][0][iy, ix].astype("float32") for k in ("PP", "T2", "magViento10", "dirViento10")}
        except Exception:
            if n == 2:
                raise
            time.sleep(10 * (n + 1))


def _grib(datos):
    import bz2

    import eccodes

    g = eccodes.codes_new_from_message(bz2.decompress(datos))
    try:
        return eccodes.codes_get_values(g)
    finally:
        eccodes.codes_release(g)


_INDICES = None


def _icon_inicio(indices):
    global _INDICES
    _INDICES = indices


def leer_icon(args):
    corrida, plazo = args
    datos = pedir(url_icon(corrida, plazo), tiempo=120)
    if datos is None:
        raise RuntimeError(f"falta ICON {nombre(corrida)} +{plazo}")
    return plazo, _grib(datos)[_INDICES].astype("float32")


def indices_icon(corrida):
    # Para cada píxel del cuadro, la celda de la grilla de triángulos del ICON más cercana.
    import numpy as np
    from scipy.spatial import cKDTree

    lat = _grib(pedir(url_icon(corrida, "CLAT"), tiempo=120))
    lon = _grib(pedir(url_icon(corrida, "CLON"), tiempo=120))
    cerca = np.flatnonzero((lat > SUR - 1) & (lat < NORTE + 1) & (lon > OESTE - 1) & (lon < ESTE + 1))
    k = math.cos(math.radians(LAT))
    arbol = cKDTree(np.c_[lat[cerca], lon[cerca] * k])
    MX, MY = _grilla_mercator()
    plat = np.degrees(2 * np.arctan(np.exp(MY / R)) - np.pi / 2)
    plon = np.degrees(MX / R)
    _, i = arbol.query(np.c_[plat.ravel(), plon.ravel() * k])
    return cerca[i].reshape(ALTO, ANCHO)


def guardar(carpeta, t, capa, valor):
    import numpy as np
    from PIL import Image

    c = CAPAS[capa]
    b = np.clip(np.rint((valor - c["desde"]) / c["paso"]), 0, 255).astype(np.uint8)
    Image.fromarray(b).save(carpeta / f"{nombre(t)}-{capa}.png", optimize=True)


def armar(salida, wrf, icon, plazos):
    from concurrent.futures import ProcessPoolExecutor
    from pathlib import Path

    import numpy as np

    sal = Path(salida)
    (sal / "wrf").mkdir(parents=True, exist_ok=True)
    (sal / "icon").mkdir(parents=True, exist_ok=True)
    meta = {
        "generado": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "caja": {"sur": SUR, "oeste": OESTE, "norte": NORTE, "este": ESTE, "proyeccion": "EPSG:3857"},
        "ancho": ANCHO,
        "alto": ALTO,
        "capas": CAPAS,
        "archivo": "{fuente}/{hora}-{capa}.png (hora UTC AAAAMMDDHH)",
        "wrf": None,
        "icon": None,
    }
    errores = []

    if wrf:
        t0 = time.time()
        c = de_nombre(wrf)
        # La lluvia viene acumulada desde el inicio: la de una hora es la resta con la hora anterior.
        pedidos = sorted(set(plazos) | {p - 1 for p in plazos if p > 0})
        try:
            with ProcessPoolExecutor(8) as ex:
                campos = dict(ex.map(leer_wrf, [(c, p) for p in pedidos]))
            horas = []
            for p in plazos:
                t = c + dt.timedelta(hours=p)
                f = campos[p]
                if p > 0:
                    guardar(sal / "wrf", t, "lluvia", np.maximum(f["PP"] - campos[p - 1]["PP"], 0))
                guardar(sal / "wrf", t, "temp", f["T2"])
                guardar(sal / "wrf", t, "viento", f["magViento10"] * 3.6)
                guardar(sal / "wrf", t, "rumbo", f["dirViento10"])
                horas.append(nombre(t))
            meta["wrf"] = {
                "corrida": wrf,
                "horas": horas,
                "capas": ["lluvia", "temp", "viento", "rumbo"],
                "sin_lluvia": [horas[0]] if plazos[0] == 0 else [],
                "fuente": "Pronóstico del SMN (modelo WRF)",
                "enlace": "https://registry.opendata.aws/smn-ar-wrf-dataset/",
            }
            print(f"WRF {wrf}: {len(horas)} horas en {time.time() - t0:.0f} s", file=sys.stderr)
        except Exception as e:
            errores.append(f"WRF {wrf}: {e!r}")

    if icon:
        t0 = time.time()
        c = de_nombre(icon)
        try:
            indices = indices_icon(c)
            with ProcessPoolExecutor(4, initializer=_icon_inicio, initargs=(indices,)) as ex:
                campos = dict(ex.map(leer_icon, [(c, p) for p in plazos]))
            horas = []
            for p in plazos:
                t = c + dt.timedelta(hours=p)
                guardar(sal / "icon", t, "nubes", campos[p])
                horas.append(nombre(t))
            meta["icon"] = {
                "corrida": icon,
                "horas": horas,
                "capas": ["nubes"],
                "fuente": "DWD (ICON)",
                "licencia": "CC BY 4.0",
                "enlace": "https://opendata.dwd.de/weather/nwp/icon/",
            }
            print(f"ICON {icon}: {len(horas)} horas en {time.time() - t0:.0f} s", file=sys.stderr)
        except Exception as e:
            errores.append(f"ICON {icon}: {e!r}")

    (sal / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    (sal / ".nojekyll").write_text("")
    peso = sum(f.stat().st_size for f in sal.rglob("*") if f.is_file())
    print(f"salida {peso / 1e6:.1f} MB", file=sys.stderr)
    if errores:
        sys.exit("falló: " + "; ".join(errores))


# --- medir (necesita requirements.txt) ---
#
# Cuánto coincidió el pronóstico con el radar, hora por hora. Solo mide: no corrige nada del modelo.
# - El radar: las imágenes de la hora ya limpias de ruido, como las ve la página (su /api/cuadro). Llovió en un punto
#   si alguna imagen de la hora lo pinta «Débil» o más (desde ~20 dBZ, ~0,6 mm/h).
# - El pronóstico: la lluvia de esa hora del WRF, de la última corrida que ya estaba publicada una hora antes de que
#   empiece (completa ~2 h 40 después de su hora y publicada hasta ~50 min más tarde). Lluvia es 0,5 mm o más, lo que
#   pinta el mapa.
# - Cada hora, lo que el radar ve bien (200 km) se parte en cajas de 20 km: una tormenta corrida unos km cuenta igual.
#   Por caja: los dos vieron lluvia (ambos), solo el pronóstico o solo el radar. Las cajas sin lluvia en ninguno no
#   cuentan: casi siempre no llueve y «acertó que no llovía» no dice nada.

RADAR_API = "https://radar-mendoza.vercel.app/api"
FILTRO = 3  # La versión del filtro de ruido que pide la página (FILTRO en src/radar/cuadros.ts).
# Colores de la escala del radar y su nivel (0 «Muy débil» … 4 «Posible granizo»), como src/radar/niveles.ts.
ESCALA_RADAR = [
    (0, (72, 61, 139)), (0, (0, 90, 0)), (1, (0, 112, 0)),
    (2, (8, 127, 219)), (2, (28, 71, 232)), (2, (110, 13, 198)),
    (3, (200, 15, 134)), (3, (192, 100, 135)), (3, (210, 136, 59)), (3, (250, 196, 49)),
    (4, (255, 253, 1)), (4, (254, 154, 88)), (4, (254, 95, 5)), (4, (253, 52, 28)), (4, (0, 0, 0)), (4, (211, 211, 211)),
]
NIVEL_LLUVIA = 1  # «Débil» o más.
LLUVIA_MM = 0.5
CAJA = 10  # celdas de 2 km por lado: cajas de 20 km.
# Hasta 200 km: más lejos el haz pasa alto y no ve la lluvia débil (del 6 al 7/11, entre 200 y 250 km el radar no vio
# lluvia en 30 horas y el modelo marcó 640 cajas: contaban como errores del pronóstico sin serlo).
ALCANCE_KM = 200
ESPERA_CORRIDA_H = 4
MIN_CUADROS = 15  # de ~30 por hora: con menos, el radar estuvo parado esa hora y no se mide.
PARADO_H = 6  # una hora con imágenes que no bajaron se reintenta hasta esto; después queda como sin radar.
TOPE_HORAS = 30  # por vuelta, para que una vuelta no se alargue.
COLUMNAS = ["hora", "corrida", "plazo", "cuadros", "ambos", "solo_pronostico", "solo_radar", "cajas", "km"]


def _alcance():
    # Las celdas dentro de los 250 km del radar y la caja de 20 km de cada una (como dentroDelAlcance en la página).
    import numpy as np

    MX, MY = _grilla_mercator()
    lat = np.degrees(2 * np.arctan(np.exp(MY / R)) - np.pi / 2)
    lon = np.degrees(MX / R)
    adentro = np.hypot((lat - LAT) * KLAT, (lon - LON) * KLON) <= ALCANCE_KM
    fila, col = np.indices((ALTO, ANCHO))
    caja = (fila // CAJA) * math.ceil(ANCHO / CAJA) + col // CAJA
    total = math.ceil(ALTO / CAJA) * math.ceil(ANCHO / CAJA)
    # Una caja cuenta si al menos la mitad de sus celdas está dentro del alcance.
    dentro = np.bincount(caja[adentro], minlength=total) * 2 >= np.bincount(caja.ravel(), minlength=total)
    return adentro, caja, total, dentro


def _radar_lluvia(datos):
    # La mitad de arriba de /api/cuadro (lo limpio) a la grilla de 2 km: True donde hay «Débil» o más.
    import io

    import numpy as np
    from PIL import Image

    img = np.asarray(Image.open(io.BytesIO(datos)).convert("RGBA"))
    img = img[: img.shape[0] // 2]
    alto, ancho = img.shape[:2]
    ys, xs = np.nonzero(img[..., 3] > 0)
    salida = np.zeros((ALTO, ANCHO), bool)
    if not len(ys):
        return salida
    rgb = img[ys, xs, :3].astype(np.int32)
    paleta = np.array([c for _, c in ESCALA_RADAR], np.int32)
    niveles = np.array([n for n, _ in ESCALA_RADAR])
    nivel = niveles[((rgb[:, None, :] - paleta[None]) ** 2).sum(-1).argmin(1)]
    si = nivel >= NIVEL_LLUVIA
    # La imagen va estirada sobre la misma caja en Mercator que la grilla: es una cuenta de proporción.
    salida[ys[si] * ALTO // alto, xs[si] * ANCHO // ancho] = True
    return salida


def _corrida_para(hora):
    # La última corrida (00/06/12/18) que ya estaba publicada ESPERA_CORRIDA_H antes de la hora, con su plazo; si esa no
    # tiene el archivo, la anterior.
    tope = hora - dt.timedelta(hours=ESPERA_CORRIDA_H)
    c = tope.replace(hour=tope.hour // 6 * 6)
    for _ in range(3):
        plazo = int((hora - c).total_seconds() // 3600) + 1  # el archivo de H+1 trae la lluvia de H a H+1
        if pedir(url_wrf(c, plazo), tiempo=20, cabeza=True) is not None:
            return c, plazo
        c -= dt.timedelta(hours=6)
    return None, None


def medir(archivo):
    import csv
    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
    from pathlib import Path

    import numpy as np

    ruta = Path(archivo)
    filas = []
    if ruta.exists():
        with ruta.open(encoding="utf-8", newline="") as f:
            # Las filas medidas con otro alcance se descartan: se vuelven a medir las que sigan en la lista del radar.
            filas = [x for x in csv.DictReader(f) if x.get("km") == str(ALCANCE_KM)]
    hechas = {f["hora"] for f in filas}
    ahora = dt.datetime.now(dt.timezone.utc)

    try:
        lista = json.loads(pedir(f"{RADAR_API}/cuadros", tiempo=30) or b"{}")
    except (ValueError, OSError) as e:
        # Sin la lista (el radar o la página caídos) no falla: las horas se miden en otra vuelta, mientras sigan en ella.
        print(f"medir: sin la lista del radar ({e!r})", file=sys.stderr)
        return
    cuadros = sorted(
        (dt.datetime.strptime(dia + png[:6], "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc), f"{dia}/{png}")
        for dia, pngs in lista.items() for png in pngs
    )
    if not cuadros:
        print("medir: la lista del radar vino vacía", file=sys.stderr)
        return
    # Desde la primera hora entera de la lista hasta la última terminada hace 10 min (las imágenes llegan con atraso).
    primera = cuadros[0][0]
    h = primera.replace(minute=0, second=0) + (dt.timedelta(hours=1) if primera.minute >= 2 else dt.timedelta())
    ultima = (ahora - dt.timedelta(minutes=70)).replace(minute=0, second=0, microsecond=0)
    horas = []
    while h <= ultima:
        if nombre(h) not in hechas:
            horas.append(h)
        h += dt.timedelta(hours=1)
    horas = horas[-TOPE_HORAS:]
    if not horas:
        print("medir: nada nuevo", file=sys.stderr)
        return

    adentro, caja, total, dentro = _alcance()
    caja_ok = dentro[caja] & adentro  # celdas que cuentan

    def bajar(r):
        try:
            return _radar_lluvia(pedir(f"{RADAR_API}/cuadro?ruta={r}&filtro={FILTRO}", tiempo=30))
        except Exception:
            return None

    corridas = {h: _corrida_para(h) for h in horas}
    archivos = sorted({(c, p) for c, plazo in corridas.values() if c for p in (plazo - 1, plazo)})
    with ProcessPoolExecutor(4) as ex:
        wrf = dict(((c, p), campos["PP"]) for (c, p), campos in zip(archivos, ex.map(_leer_pp, archivos)))

    nuevas = []
    for h in horas:
        rutas = [r for t, r in cuadros if h <= t < h + dt.timedelta(hours=1)]
        vieja = ahora - h > dt.timedelta(hours=PARADO_H + 1)
        fila = {"hora": nombre(h), "corrida": "", "plazo": "", "cuadros": len(rutas), "km": ALCANCE_KM}
        c, plazo = corridas[h]
        if len(rutas) >= MIN_CUADROS and c is None and not vieja:
            continue  # todavía no está la corrida en S3: se reintenta
        if len(rutas) >= MIN_CUADROS and c is not None:
            with ThreadPoolExecutor(8) as ex:
                imagenes = [x for x in ex.map(bajar, rutas) if x is not None]
            if len(imagenes) < len(rutas) * 0.8 and not vieja:
                continue  # fallaron pedidos: se reintenta en la próxima vuelta
            fila["cuadros"] = len(imagenes)
            if len(imagenes) >= MIN_CUADROS:
                radar = np.logical_or.reduce(imagenes) & caja_ok
                pron = (np.maximum(wrf[(c, plazo)] - wrf[(c, plazo - 1)], 0) >= LLUVIA_MM) & caja_ok
                en_radar = np.bincount(caja[radar], minlength=total) > 0
                en_pron = np.bincount(caja[pron], minlength=total) > 0
                fila.update(
                    corrida=nombre(c), plazo=plazo,
                    ambos=int((en_radar & en_pron).sum()),
                    solo_pronostico=int((en_pron & ~en_radar).sum()),
                    solo_radar=int((en_radar & ~en_pron).sum()),
                    cajas=int(dentro.sum()),
                )
        nuevas.append(fila)
        print(f"medir {fila}", file=sys.stderr)

    filas = sorted(filas + nuevas, key=lambda f: f["hora"])
    with ruta.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, COLUMNAS, lineterminator="\n")
        w.writeheader()
        w.writerows(filas)


def _leer_pp(args):
    return leer_wrf(args)[1]


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["medir"]:
        medir(a[1])
    elif a[:1] == ["ver"]:
        ver(a[1], "--forzar" in a)
    elif a[:1] == ["armar"]:
        opc = dict(zip(a[2::2], a[3::2]))
        plazos = [int(x) for x in opc["--plazos"].split(",")] if "--plazos" in opc else list(PLAZOS)
        armar(a[1], opc.get("--wrf"), opc.get("--icon"), plazos)
    else:
        sys.exit("uso: ver URL | armar SALIDA --wrf AAAAMMDDHH --icon AAAAMMDDHH | medir acierto.csv")
