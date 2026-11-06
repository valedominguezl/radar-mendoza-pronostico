# Pronóstico para Radar Mendoza: baja la última corrida del WRF del SMN (lluvia, temperatura y viento) y del ICON del
# DWD (nubes), recorta el cuadro del radar, lo pasa a Mercator y guarda un PNG gris de 8 bits por capa y hora, más
# meta.json con las corridas, las horas y cómo pasar cada byte a su unidad (valor = desde + paso × byte).
#
#   python pronostico.py ver URL_DEL_META_PUBLICADO [--forzar]   → renglones clave=valor para GITHUB_OUTPUT
#   python pronostico.py armar SALIDA --wrf AAAAMMDDHH --icon AAAAMMDDHH [--plazos 0,1,24]
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


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["ver"]:
        ver(a[1], "--forzar" in a)
    elif a[:1] == ["armar"]:
        opc = dict(zip(a[2::2], a[3::2]))
        plazos = [int(x) for x in opc["--plazos"].split(",")] if "--plazos" in opc else list(PLAZOS)
        armar(a[1], opc.get("--wrf"), opc.get("--icon"), plazos)
    else:
        sys.exit(__doc__ or "uso: ver URL | armar SALIDA --wrf AAAAMMDDHH --icon AAAAMMDDHH")
