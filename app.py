import streamlit as st
import pandas as pd
import os
from datetime import datetime
import urllib.parse
import re
import unicodedata
import json
import html as html_lib
import time
import sqlite3

# =========================================================
# ✅ KOKELUCHE OS (SQLite) — GARZÓN + COCINA + ADMIN
# =========================================================
# Ejecutar:
#   streamlit run app.py
#
# Para celular/tablet desde la misma Wi-Fi:
#   streamlit run app.py --server.address 0.0.0.0 --server.port 8501
#
# Para usar fuera de tu red:
#   (Recomendado) cloudflared tunnel --url http://localhost:8501
# =========================================================

APP_TITLE = "Kokeluche OS v50 (SQLite)"
DATA_FOLDER = "data"
LOGO_PATH = "logo.png"

DEFAULT_DELIVERY_FEE = 500
DEFAULT_WA_COOK_NUM = "56989803518"

ADMIN_PASSWORD = os.getenv("KOKELUCHE_ADMIN_PASS", "1234")
KITCHEN_PASSWORD = os.getenv("KOKELUCHE_KITCHEN_PASS", "")  # si vacío, no pide clave

os.makedirs(DATA_FOLDER, exist_ok=True)
DB_PATH = os.path.join(DATA_FOLDER, "kokeluche.db")


# =========================================================
# 1) SQLite: conexión + init
# =========================================================
def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def db_init():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,               -- plato, bebida, acomp, fee
        name TEXT NOT NULL UNIQUE,
        price INTEGER NOT NULL DEFAULT 0,
        stock INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tables_status (
        mesa TEXT PRIMARY KEY,
        estado TEXT NOT NULL DEFAULT 'Libre',    -- Libre/Ocupada
        origen TEXT NOT NULL DEFAULT '',
        detalle TEXT NOT NULL DEFAULT '',
        detalle_items TEXT NOT NULL DEFAULT '',
        total INTEGER NOT NULL DEFAULT 0,
        garzon TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        origen TEXT NOT NULL,
        tipo TEXT NOT NULL,                      -- Local/Entrega
        ubicacion TEXT NOT NULL,
        detalle TEXT NOT NULL,
        detalle_items TEXT NOT NULL,
        total INTEGER NOT NULL,
        pago TEXT NOT NULL,
        garzon TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """)

    conn.commit()
    conn.close()


def q_all(sql, params=()):
    conn = db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def q_one(sql, params=()):
    conn = db()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return dict(row) if row else None


def exec_sql(sql, params=()):
    conn = db()
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    last = cur.lastrowid
    conn.close()
    return last


def exec_many(sql, seq_params):
    conn = db()
    cur = conn.cursor()
    cur.executemany(sql, seq_params)
    conn.commit()
    conn.close()


def settings_get(key: str, default: str) -> str:
    row = q_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def settings_set(key: str, value: str) -> None:
    exec_sql("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def seed_if_empty():
    # settings
    if not q_one("SELECT 1 FROM settings WHERE key='delivery_fee'"):
        settings_set("delivery_fee", str(DEFAULT_DELIVERY_FEE))
    if not q_one("SELECT 1 FROM settings WHERE key='wa_cocina'"):
        settings_set("wa_cocina", DEFAULT_WA_COOK_NUM)

    # garzones (simple: guardados en settings como CSV json, para mantenerlo simple sin tabla extra)
    if not q_one("SELECT 1 FROM settings WHERE key='garzones_json'"):
        settings_set("garzones_json", json.dumps(["Garzón 1", "Garzón 2"], ensure_ascii=False))

    # menu_items
    c = q_one("SELECT COUNT(*) as n FROM menu_items")
    if c and int(c["n"]) == 0:
        exec_many(
            "INSERT INTO menu_items(type,name,price,stock,active) VALUES(?,?,?,?,1)",
            [
                ("plato", "Pollo asado", 5500, 30),
                ("bebida", "Jugo Piña", 2500, 30),
                ("acomp", "Papas fritas", 0, 30),
                ("acomp", "❌ SIN ACOMP.", 0, 999999),
            ]
        )

    # mesas
    t = q_one("SELECT COUNT(*) as n FROM tables_status")
    if t and int(t["n"]) == 0:
        exec_many("INSERT INTO tables_status(mesa) VALUES(?)", [(f"Mesa {i}",) for i in range(1, 9)])


db_init()
seed_if_empty()


# =========================================================
# 2) Helpers
# =========================================================
def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_text(s: str) -> str:
    s = "" if s is None else str(s)
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s


def wa_link(numero: str, mensaje: str) -> str:
    n = re.sub(r"\D", "", str(numero))
    return f"https://wa.me/{n}?text={urllib.parse.quote(mensaje)}"


def safe_load_items(s: str):
    try:
        if not s or str(s).strip() == "":
            return None
        data = json.loads(s)
        if isinstance(data, list):
            out = []
            for it in data:
                if isinstance(it, dict) and "item" in it and "precio" in it:
                    out.append(it)
            return out
        return None
    except Exception:
        return None


def extraer_direccion(texto: str) -> str:
    t = str(texto or "").strip()
    m = re.search(r"(?:direcci[oó]n)\s*[:\-]\s*(.+)$", t, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:entrega|entregar|env[ií]o)\s+(?:a|en)\s+(.+)$", t, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def build_ticket_agg(items: list[dict], subtotal: int):
    propina = int(round(subtotal * 0.10))
    total_con_propina = int(subtotal + propina)

    if not items:
        return {"lines": ["(sin desglose guardado)"], "propina": propina, "total_propina": total_con_propina}

    agg = {}
    fallback_simple = False

    for it in items:
        t = it.get("type", "")
        qty = int(it.get("qty", 1)) if isinstance(it.get("qty", 1), (int, float, str)) else 1
        try:
            qty = int(qty)
        except:
            qty = 1

        unit = it.get("unit_price", None)

        if unit is None and t != "fee":
            if "unit_price" not in it and "qty" not in it:
                fallback_simple = True
                break

        if t == "plato":
            pl = it.get("plato", "")
            ac = it.get("acomp", "❌ SIN ACOMP.")
            label = f"{pl} + {ac}" if ac else pl
            key = f"plato::{label}"
            unit_val = int(it.get("unit_price", 0))
        elif t == "bebida":
            be = it.get("bebida", "")
            label = be
            key = f"bebida::{label}"
            unit_val = int(it.get("unit_price", 0))
        elif t == "acomp":
            ac = it.get("acomp", "")
            label = f"Acomp: {ac}"
            key = f"acomp::{label}"
            unit_val = int(it.get("unit_price", 0))
        elif t == "fee":
            label = it.get("item", "Delivery")
            key = f"fee::{label}"
            unit_val = int(it.get("precio", 0))
            qty = 1
        else:
            label = it.get("item", "Item")
            key = f"other::{label}"
            unit_val = int(it.get("unit_price", it.get("precio", 0)))

        if key not in agg:
            agg[key] = {"label": label, "qty": 0, "unit": unit_val, "total": 0}

        agg[key]["qty"] += qty
        agg[key]["total"] += qty * unit_val

    if fallback_simple:
        lines = [f"- {x.get('item','')}  ${int(x.get('precio',0)):,}" for x in items]
        return {"lines": lines, "propina": propina, "total_propina": total_con_propina}

    order_key = {"plato": 1, "acomp": 2, "bebida": 3, "fee": 4, "other": 5}

    def sort_fn(k):
        prefix = k.split("::", 1)[0]
        return (order_key.get(prefix, 99), agg[k]["label"])

    lines = []
    for k in sorted(agg.keys(), key=sort_fn):
        a = agg[k]
        if a["qty"] > 1:
            lines.append(f"- {a['qty']} x {a['label']}  (${a['unit']:,} c/u)  =  ${a['total']:,}")
        else:
            lines.append(f"- {a['label']}  =  ${a['total']:,}")

    return {"lines": lines, "propina": propina, "total_propina": total_con_propina}


def build_ticket_text(fecha_str, mesa, garzon, items, subtotal):
    t = build_ticket_agg(items, subtotal)
    lines = []
    lines.append("🧾 KOKELUCHE TICKET")
    lines.append(f"Fecha: {fecha_str}")
    lines.append(f"Mesa: {mesa}")
    lines.append(f"Atiende: {garzon}")
    lines.append("-" * 34)
    lines.extend(t["lines"])
    lines.append("-" * 34)
    lines.append(f"SUBTOTAL:             ${int(subtotal):,}")
    lines.append(f"PROPINA SUG. (10%):   ${int(t['propina']):,}")
    lines.append(f"TOTAL + PROPINA:      ${int(t['total_propina']):,}")
    return "\n".join(lines), t["propina"], t["total_propina"]


def build_wa_order_message(
    *,
    modalidad: str,
    origen: str,
    garzon: str,
    items: list[dict],
    total: int,
    mesa: str = "",
    direccion: str = "",
    delivery_fee: int = 0
) -> str:
    titulo = "*🧾 KOKELUCHE — PEDIDO*"
    linea = "──────────────"
    hora = datetime.now().strftime("%H:%M")

    encabezado = [
        titulo,
        linea,
        f"🕒 {hora}",
        f"👤 Garzón: {garzon}",
        f"📍 Origen: {origen}",
    ]

    if modalidad == "🏠 Local":
        encabezado.append(f"🍽️ Mesa: {mesa}")
    else:
        encabezado.append("🛵 Modalidad: Entrega")
        if direccion:
            encabezado.append(f"📌 Dirección: {direccion}")

    detalle = [linea, "*📦 Detalle:*"]
    for it in items:
        detalle.append(f"• {it.get('item','')}")

    totales = [linea]
    if modalidad == "🛵 Entrega" and delivery_fee:
        totales.append(f"🚚 Delivery: ${int(delivery_fee):,}")
    totales.append(f"💰 *TOTAL: ${int(total):,}*")

    return "\n".join(encabezado + detalle + totales)


# =========================================================
# 3) Stock transaccional (SQLite)
# =========================================================
def stock_apply_sqlite(items: list[dict], mode: str) -> tuple[bool, str]:
    """
    mode:
      - 'debit' descuenta (valida stock)
      - 'credit' repone
    """
    need = {}

    for it in items:
        t = it.get("type", "")
        qty = it.get("qty", 1)
        try:
            qty = int(qty)
        except:
            qty = 1
        qty = max(1, qty)

        if t == "plato":
            pl = it.get("plato", "")
            if pl:
                need[pl] = need.get(pl, 0) + qty
            ac = it.get("acomp", "")
            if ac and ac != "❌ SIN ACOMP.":
                need[ac] = need.get(ac, 0) + qty

        elif t == "acomp":
            ac = it.get("acomp", "")
            if ac and ac != "❌ SIN ACOMP.":
                need[ac] = need.get(ac, 0) + qty

        elif t == "bebida":
            be = it.get("bebida", "")
            if be:
                need[be] = need.get(be, 0) + qty

        # fee: no stock

    sign = -1 if mode == "debit" else +1

    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")

        if mode == "debit":
            for name, q in need.items():
                row = conn.execute("SELECT stock, active FROM menu_items WHERE name=?", (name,)).fetchone()
                if not row:
                    conn.execute("ROLLBACK")
                    return False, f"No existe en menú: {name}"
                if int(row["active"]) != 1:
                    conn.execute("ROLLBACK")
                    return False, f"'{name}' está inactivo."
                if int(row["stock"]) < int(q):
                    conn.execute("ROLLBACK")
                    return False, f"❌ Sin stock suficiente de '{name}'. Stock: {int(row['stock'])}, requerido: {q}"

        for name, q in need.items():
            conn.execute(
                "UPDATE menu_items SET stock = MAX(stock + ?, 0) WHERE name=?",
                (sign * int(q), name)
            )

        conn.commit()
        return True, ""
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except:
            pass
        return False, f"DB error: {e}"
    finally:
        conn.close()


# =========================================================
# 4) Session State
# =========================================================
defaults = {
    "pedido_temporal": [],
    "agregados_temporal": [],
    "mesa_sel": "",
    "estado_sel": "",
    "direccion_wa": "",
    "texto_ws_temp": "",
    "last_wa_link": "",
    "last_wa_msg": "",
    "ws_warnings": [],
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v


# =========================================================
# 5) Data loaders (desde DB)
# =========================================================
def load_menu_dfs():
    df_platos = pd.DataFrame(q_all("SELECT name AS Plato, price AS Precio, stock AS Stock FROM menu_items WHERE type='plato' AND active=1"))
    df_bebidas = pd.DataFrame(q_all("SELECT name AS Producto, price AS Precio, stock AS Stock FROM menu_items WHERE type='bebida' AND active=1"))
    df_acomp = pd.DataFrame(q_all("SELECT name AS Acompañamiento, price AS Precio, stock AS Stock FROM menu_items WHERE type='acomp' AND active=1"))
    return df_platos, df_bebidas, df_acomp


def load_mesas_df():
    return pd.DataFrame(q_all("""
        SELECT mesa AS Mesa, estado AS Estado, origen AS Origen, detalle AS Detalle, detalle_items AS DetalleItems, total AS Total, garzon AS Garzon
        FROM tables_status
        ORDER BY CAST(REPLACE(mesa,'Mesa ','') AS INT)
    """))


def load_ventas_df():
    return pd.DataFrame(q_all("""
        SELECT created_at AS Fecha, origen AS Origen, tipo AS Tipo, ubicacion AS Ubicacion, detalle AS Detalle, detalle_items AS DetalleItems, total AS Total, pago AS Pago, garzon AS Garzon
        FROM sales
        ORDER BY id DESC
    """))


def load_garzones_list():
    raw = settings_get("garzones_json", "[]")
    try:
        g = json.loads(raw)
        g = [str(x).strip() for x in g if str(x).strip()]
        return g if g else ["Garzón 1", "Garzón 2"]
    except:
        return ["Garzón 1", "Garzón 2"]


# =========================================================
# 6) Lector WhatsApp (con stock + cantidades)
# =========================================================
def lector_inteligente(df_platos, df_bebidas, df_acomp):
    st.session_state.ws_warnings = []
    texto_raw = st.session_state.get("ws_input", "")
    texto = normalize_text(texto_raw)
    if not texto:
        return

    encontrados = []

    dir_encontrada = extraer_direccion(texto_raw)
    if dir_encontrada:
        st.session_state.direccion_wa = dir_encontrada

    # Platos
    for _, row in df_platos.iterrows():
        nombre = str(row["Plato"])
        nombre_n = normalize_text(nombre)
        if not nombre_n:
            continue

        pat_qty = rf"(?:^|\s)(\d+)\s*x?\s*{re.escape(nombre_n)}(?:\s|$)"
        m_qty = re.search(pat_qty, texto)
        qty = int(m_qty.group(1)) if m_qty else (1 if nombre_n in texto else 0)
        if qty <= 0:
            continue

        stock_pl = int(row["Stock"])
        if stock_pl < qty:
            st.session_state.ws_warnings.append(f"❌ '{nombre}' sin stock suficiente (Stock {stock_pl}, pedido {qty}).")
            continue

        ac_en = "❌ SIN ACOMP."
        pr_ac = 0

        for _, row_ac in df_acomp.iterrows():
            ac_nom = str(row_ac["Acompañamiento"])
            ac_n = normalize_text(ac_nom)
            if not ac_n or ac_nom == "❌ SIN ACOMP.":
                continue

            if ac_n in texto:
                stock_ac = int(row_ac["Stock"])
                if stock_ac < qty:
                    st.session_state.ws_warnings.append(f"❌ '{ac_nom}' sin stock suficiente (Stock {stock_ac}, requerido {qty}).")
                    ac_en = "❌ SIN ACOMP."
                    pr_ac = 0
                else:
                    ac_en = ac_nom
                    pr_ac = int(row_ac["Precio"])
                break

        unit = int(row["Precio"]) + int(pr_ac)
        total = unit * qty
        label = f"{qty} x {nombre} + {ac_en}" if qty > 1 else f"{nombre} + {ac_en}"

        encontrados.append({
            "item": label, "precio": int(total),
            "type": "plato", "plato": nombre, "acomp": ac_en,
            "qty": int(qty), "unit_price": int(unit)
        })

    # Bebidas
    for _, row_b in df_bebidas.iterrows():
        prod = str(row_b["Producto"])
        prod_n = normalize_text(prod)
        if not prod_n:
            continue

        pat_qty = rf"(?:^|\s)(\d+)\s*x?\s*{re.escape(prod_n)}(?:\s|$)"
        m_qty = re.search(pat_qty, texto)
        qty = int(m_qty.group(1)) if m_qty else (1 if prod_n in texto else 0)
        if qty <= 0:
            continue

        stock_be = int(row_b["Stock"])
        if stock_be < qty:
            st.session_state.ws_warnings.append(f"❌ '{prod}' sin stock suficiente (Stock {stock_be}, pedido {qty}).")
            continue

        unit = int(row_b["Precio"])
        total = unit * qty
        label = f"{qty} x {prod}" if qty > 1 else prod

        encontrados.append({
            "item": label, "precio": int(total),
            "type": "bebida", "bebida": prod,
            "qty": int(qty), "unit_price": int(unit)
        })

    if encontrados:
        st.session_state.pedido_temporal.extend(encontrados)


# =========================================================
# 7) UI: estilos + sidebar
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")

st.markdown(
    """
    <style>
    .stApp { background-color: #f8f9fa; }
    .stButton>button { border-radius: 12px; font-weight: 800; width: 100%; height: 3.2em; }
    .card { background-color: white; padding: 18px; border-radius: 15px; box-shadow: 0px 4px 12px rgba(0,0,0,0.05); margin-bottom: 16px; border: 1px solid #eee; }
    .price-tag { background-color: #fff1f2; color: #E63946; padding: 14px; border-radius: 12px; font-weight: 900; text-align: center; font-size: 26px; border: 2px solid #E63946; }
    .item-pedido { background-color: #f1f3f5; padding: 8px 10px; border-radius: 8px; margin-bottom: 6px; border-left: 5px solid #25d366; display: flex; justify-content: space-between; font-weight: 800; }
    .ticket-box { background-color: #fff; border: 1px dashed #000; padding: 16px; font-family: 'Courier New', Courier, monospace; color: #000; }
    .info { background-color: #e3f2fd; padding: 10px; border-radius: 8px; margin-bottom: 10px; color: #0d47a1; font-weight: 800; }
    .hint { color:#6c757d; font-size: 12px; }
    </style>
    """,
    unsafe_allow_html=True
)

delivery_fee = int(settings_get("delivery_fee", str(DEFAULT_DELIVERY_FEE)))
wa_default = settings_get("wa_cocina", DEFAULT_WA_COOK_NUM)

with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH)

    perfil = st.radio("PERFIL:", ["👨‍🍳 Garzón", "👩‍🍳 Cocina", "🔐 Admin"])

    cocina_ok = True
    if perfil == "👩‍🍳 Cocina" and KITCHEN_PASSWORD.strip():
        cocina_ok = (st.text_input("Clave Cocina", type="password") == KITCHEN_PASSWORD)

    admin_ok = False
    if perfil == "🔐 Admin":
        admin_ok = (st.text_input("Clave Admin", type="password") == ADMIN_PASSWORD)

    num_ws = st.text_input("WhatsApp Cocina:", wa_default)
    if num_ws != wa_default:
        settings_set("wa_cocina", num_ws)

    st.caption(f"Delivery actual: ${delivery_fee:,}")

    if st.session_state.last_wa_link:
        st.success("Pedido listo para enviar a cocina.")
        st.markdown(f"[📲 Enviar a cocina por WhatsApp]({st.session_state.last_wa_link})")
        if st.button("✅ Cerrar aviso"):
            st.session_state.last_wa_link = ""
            st.session_state.last_wa_msg = ""


# =========================================================
# 8) UI component: agregar items (con stock)
# =========================================================
def ui_agregar_items(lista_key: str, df_platos, df_bebidas, df_acomp):
    st.session_state.setdefault(lista_key, [])

    tipo_p = st.radio(
        "Producto:",
        ["Plato Fondo", "Bebida/Jugo", "Acompañamiento solo"],
        horizontal=True,
        key=f"tp_{lista_key}"
    )

    if tipo_p == "Plato Fondo":
        platos_disp = df_platos[df_platos["Stock"] > 0]["Plato"].tolist()
        if not platos_disp:
            st.error("❌ No hay platos disponibles (stock = 0).")
            return

        pl = st.selectbox("Plato:", platos_disp, key=f"pl_{lista_key}")

        acomp_disp = ["❌ SIN ACOMP."] + df_acomp[(df_acomp["Stock"] > 0) & (df_acomp["Acompañamiento"] != "❌ SIN ACOMP.")]["Acompañamiento"].tolist()
        ac = st.selectbox("Acomp:", acomp_disp, key=f"ac_{lista_key}")

        stock_pl = int(df_platos.loc[df_platos["Plato"] == pl, "Stock"].values[0])
        stock_ac = 999999
        if ac != "❌ SIN ACOMP." and ac in df_acomp["Acompañamiento"].values:
            stock_ac = int(df_acomp.loc[df_acomp["Acompañamiento"] == ac, "Stock"].values[0])

        max_qty = max(1, min(stock_pl, stock_ac))
        qty = st.number_input("Cantidad", min_value=1, max_value=max_qty, value=1, step=1, key=f"qty_pl_{lista_key}")
        st.caption(f"Stock plato: {stock_pl} | Stock acomp: {('∞' if ac=='❌ SIN ACOMP.' else stock_ac)}")

        if st.button("Sumar", key=f"add_pl_{lista_key}"):
            p_pl = int(df_platos.loc[df_platos["Plato"] == pl, "Precio"].values[0])
            p_ac = 0
            if ac != "❌ SIN ACOMP.":
                p_ac = int(df_acomp.loc[df_acomp["Acompañamiento"] == ac, "Precio"].values[0])

            unit = p_pl + p_ac
            total = unit * int(qty)
            label = f"{qty} x {pl} + {ac}" if qty > 1 else f"{pl} + {ac}"

            st.session_state[lista_key].append({
                "item": label, "precio": int(total),
                "type": "plato", "plato": pl, "acomp": ac,
                "qty": int(qty), "unit_price": int(unit)
            })
            st.rerun()

    elif tipo_p == "Bebida/Jugo":
        beb_disp = df_bebidas[df_bebidas["Stock"] > 0]["Producto"].tolist()
        if not beb_disp:
            st.error("❌ No hay bebidas disponibles (stock = 0).")
            return

        be = st.selectbox("Líquido:", beb_disp, key=f"be_{lista_key}")
        stock_be = int(df_bebidas.loc[df_bebidas["Producto"] == be, "Stock"].values[0])
        qty = st.number_input("Cantidad", min_value=1, max_value=max(1, stock_be), value=1, step=1, key=f"qty_be_{lista_key}")
        st.caption(f"Stock bebida: {stock_be}")

        if st.button("Sumar", key=f"add_be_{lista_key}"):
            p = int(df_bebidas.loc[df_bebidas["Producto"] == be, "Precio"].values[0])
            unit = p
            total = unit * int(qty)
            label = f"{qty} x {be}" if qty > 1 else be

            st.session_state[lista_key].append({
                "item": label, "precio": int(total),
                "type": "bebida", "bebida": be,
                "qty": int(qty), "unit_price": int(unit)
            })
            st.rerun()

    else:
        acomp_disp = df_acomp[(df_acomp["Stock"] > 0) & (df_acomp["Acompañamiento"] != "❌ SIN ACOMP.")]["Acompañamiento"].tolist()
        if not acomp_disp:
            st.error("❌ No hay agregados/acomp disponibles (stock = 0).")
            return

        ac_s = st.selectbox("Acomp:", acomp_disp, key=f"acs_{lista_key}")
        stock_ac = int(df_acomp.loc[df_acomp["Acompañamiento"] == ac_s, "Stock"].values[0])
        qty = st.number_input("Cantidad", min_value=1, max_value=max(1, stock_ac), value=1, step=1, key=f"qty_ac_{lista_key}")
        st.caption(f"Stock acomp: {stock_ac}")

        if st.button("Sumar", key=f"add_ac_{lista_key}"):
            p = int(df_acomp.loc[df_acomp["Acompañamiento"] == ac_s, "Precio"].values[0])
            unit = p
            total = unit * int(qty)
            label = f"{qty} x Acomp: {ac_s}" if qty > 1 else f"Acomp: {ac_s}"

            st.session_state[lista_key].append({
                "item": label, "precio": int(total),
                "type": "acomp", "acomp": ac_s,
                "qty": int(qty), "unit_price": int(unit)
            })
            st.rerun()


# =========================================================
# 9) COCINA (solo lectura)
# =========================================================
if perfil == "👩‍🍳 Cocina":
    if not cocina_ok:
        st.warning("Clave cocina incorrecta.")
        st.stop()

    st.title("👩‍🍳 Cocina — Pedidos en vivo")

    c1, c2, c3 = st.columns([2, 2, 3])
    if c1.button("🔄 Actualizar ahora"):
        st.rerun()

    auto = c2.toggle("Auto-actualizar", value=False)
    secs = c3.selectbox("Cada (segundos)", [5, 10, 15, 30, 60], index=1)

    mesas_k = load_mesas_df()
    ventas_k = load_ventas_df()

    st.write("---")

    ocupadas = mesas_k[mesas_k["Estado"] == "Ocupada"].copy()
    st.subheader(f"🍽️ Mesas ocupadas ({len(ocupadas)})")

    if ocupadas.empty:
        st.info("No hay mesas ocupadas en este momento.")
    else:
        for _, r in ocupadas.iterrows():
            mesa = r["Mesa"]
            garzon = r["Garzon"]
            origen = r["Origen"] or "-"
            subtotal = int(r["Total"])
            items = safe_load_items(r["DetalleItems"]) or []
            agg = build_ticket_agg(items, subtotal)

            st.markdown("<div class='card'>", unsafe_allow_html=True)
            st.markdown(f"### 🍽️ {mesa}", unsafe_allow_html=True)
            st.markdown(f"**👤 Garzón:** {garzon}  \n**📍 Origen:** {origen}", unsafe_allow_html=True)
            st.markdown("---")
            st.markdown("**📦 Detalle:**")
            for line in agg["lines"]:
                st.write(line)
            st.markdown("---")
            st.markdown(f"**💰 SUBTOTAL:** ${subtotal:,}")
            st.markdown("</div>", unsafe_allow_html=True)

    st.write("---")

    pend = ventas_k[(ventas_k["Tipo"] == "Entrega") & (ventas_k["Pago"] == "Pendiente")].copy()
    st.subheader(f"🛵 Entregas pendientes ({len(pend)})")

    if pend.empty:
        st.info("No hay entregas pendientes.")
    else:
        for _, r in pend.head(25).iterrows():
            addr = r["Ubicacion"]
            garzon = r["Garzon"]
            total = int(r["Total"])
            origen = r["Origen"] or "-"
            items = safe_load_items(r["DetalleItems"]) or []

            with st.expander(f"📦 {addr} — ${total:,}  |  👤 {garzon}  |  {origen}"):
                agg = build_ticket_agg(items, total)
                for line in agg["lines"]:
                    st.write(line)
                st.write(f"💰 TOTAL: **${total:,}**")
                if str(addr).strip():
                    maps = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(str(addr))}"
                    st.markdown(f"[📍 Abrir en Maps]({maps})")

    if auto:
        time.sleep(int(secs))
        st.rerun()

    st.stop()


# =========================================================
# 10) GARZÓN
# =========================================================
df_platos, df_bebidas, df_acomp = load_menu_dfs()
mesas_status = load_mesas_df()
ventas = load_ventas_df()
garzones_list = load_garzones_list()

if perfil == "👨‍🍳 Garzón":
    st.title("🍴 Panel de Ventas")

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    garzon_turno = st.selectbox("👤 Atendido por:", garzones_list)
    st.markdown("</div>", unsafe_allow_html=True)

    c1, c2 = st.columns(2)
    origen = c1.radio("1. Origen del Pedido:", ["🙋‍♂️ Presencial", "📥 WhatsApp"], horizontal=True, key="orig_r")
    modalidad = c2.selectbox("2. Modalidad:", ["(selecciona)", "🏠 Local", "🛵 Entrega"], index=0, key="mod_sel")

    if modalidad == "(selecciona)":
        st.info("Selecciona la modalidad para continuar.")
        st.stop()

    st.write("---")

    if origen == "📥 WhatsApp":
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        txt_input = st.text_area("Pega mensaje aquí:", key="ws_input", value=st.session_state.texto_ws_temp)
        st.session_state.texto_ws_temp = txt_input

        if st.button("🔍 CARGAR PEDIDO DE WHATSAPP"):
            lector_inteligente(df_platos, df_bebidas, df_acomp)
            st.success("Analizado.")

        if st.session_state.ws_warnings:
            for w in st.session_state.ws_warnings:
                st.warning(w)

        st.markdown("<div class='hint'>Tip: usa “dirección: ...” o “entrega a ...” para detectar dirección.</div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    direccion = ""
    if modalidad == "🛵 Entrega":
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        direccion = st.text_input("📍 Dirección de Envío:", value=st.session_state.direccion_wa)
        st.markdown("</div>", unsafe_allow_html=True)

    if modalidad == "🏠 Local":
        st.subheader("📍 Seleccione Mesa")
        m_cols = st.columns(4)

        for i, row in mesas_status.iterrows():
            with m_cols[i % 4]:
                estado = row["Estado"]
                emoji = "🟢" if estado == "Libre" else "🔴"
                if st.button(f"{emoji} {row['Mesa']} ({estado})", key=f"m_{i}"):
                    st.session_state.mesa_sel = row["Mesa"]
                    st.session_state.estado_sel = row["Estado"]

    puede_operar = (modalidad == "🛵 Entrega") or (modalidad == "🏠 Local" and st.session_state.mesa_sel != "")
    if not puede_operar:
        st.stop()

    st.write("---")

    # ==========================
    # Mesa ocupada
    # ==========================
    if modalidad == "🏠 Local" and st.session_state.estado_sel == "Ocupada":
        mesa_sel = st.session_state.mesa_sel
        mesa_row = q_one("""
            SELECT mesa, estado, origen, detalle, detalle_items, total, garzon
            FROM tables_status WHERE mesa=?
        """, (mesa_sel,))
        if not mesa_row:
            st.error("Mesa no encontrada.")
            st.stop()

        st.markdown(
            f"<div class='info'>Mesa atendida por: {mesa_row['garzon']} | Origen: {mesa_row['origen'] or '-'}</div>",
            unsafe_allow_html=True
        )

        subtotal_mesa = int(mesa_row["total"])
        propina_sug = int(round(subtotal_mesa * 0.10))
        total_con_propina = int(subtotal_mesa + propina_sug)

        st.markdown(f"<div class='price-tag'>SUBTOTAL MESA: ${subtotal_mesa:,}</div>", unsafe_allow_html=True)
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.write(f"💡 Propina sugerida (10%): **${propina_sug:,}**")
        st.write(f"🧾 Total con propina: **${total_con_propina:,}**")
        st.markdown("</div>", unsafe_allow_html=True)

        t_col1, t_col2, t_col3 = st.columns(3)

        if t_col1.button("📄 GENERAR TICKET (IMPRIMIR)"):
            items = safe_load_items(mesa_row["detalle_items"])
            fecha_str = datetime.now().strftime("%d/%m/%Y %H:%M")
            ticket_text, _, _ = build_ticket_text(
                fecha_str=fecha_str,
                mesa=mesa_sel,
                garzon=mesa_row["garzon"],
                items=items,
                subtotal=subtotal_mesa
            )
            st.markdown(
                f"<div class='ticket-box'><pre>{html_lib.escape(ticket_text)}</pre></div>",
                unsafe_allow_html=True
            )

        if t_col2.button("🚫 ANULAR MESA (REPONE STOCK)"):
            items = safe_load_items(mesa_row["detalle_items"]) or []
            ok, msg = stock_apply_sqlite(items, mode="credit")
            if not ok:
                st.error(msg)
                st.stop()

            exec_sql("""
                UPDATE tables_status
                SET estado='Libre', origen='', detalle='', detalle_items='', total=0, garzon='', updated_at=datetime('now')
                WHERE mesa=?
            """, (mesa_sel,))
            st.session_state.mesa_sel = ""
            st.session_state.estado_sel = ""
            st.session_state.agregados_temporal = []
            st.rerun()

        if t_col3.button("🧹 LIMPIAR VISTA"):
            st.session_state.agregados_temporal = []
            st.rerun()

        st.subheader("➕ Agregar consumo a esta mesa")

        if st.session_state.agregados_temporal:
            for i, it in enumerate(st.session_state.agregados_temporal):
                col_i, col_d = st.columns([5, 1])
                col_i.markdown(
                    f"<div class='item-pedido'>{it['item']} <span>${int(it['precio']):,}</span></div>",
                    unsafe_allow_html=True
                )
                if col_d.button("🗑️", key=f"del_add_{i}"):
                    st.session_state.agregados_temporal.pop(i)
                    st.rerun()

        with st.expander("➕ Agregar Platos / Bebidas (mesa ocupada)"):
            df_platos, df_bebidas, df_acomp = load_menu_dfs()
            ui_agregar_items("agregados_temporal", df_platos, df_bebidas, df_acomp)

        add_total = sum(int(i["precio"]) for i in st.session_state.agregados_temporal)
        if add_total > 0:
            st.markdown(f"<div class='price-tag'>AGREGADOS: ${add_total:,}</div>", unsafe_allow_html=True)

            if st.button("✅ CONFIRMAR AGREGADOS A LA MESA (DESCUENTA STOCK)"):
                ok, msg = stock_apply_sqlite(st.session_state.agregados_temporal, mode="debit")
                if not ok:
                    st.error(msg)
                    st.stop()

                det_ant = str(mesa_row["detalle"]).strip()
                det_new = " / ".join([x["item"] for x in st.session_state.agregados_temporal])
                det_final = (det_ant + " / " + det_new) if det_ant else det_new

                old_items = safe_load_items(mesa_row["detalle_items"]) or []
                new_items = old_items + list(st.session_state.agregados_temporal)

                exec_sql("""
                    UPDATE tables_status
                    SET detalle=?, detalle_items=?, total=total+?, updated_at=datetime('now')
                    WHERE mesa=?
                """, (
                    det_final,
                    json.dumps(new_items, ensure_ascii=False),
                    int(add_total),
                    mesa_sel
                ))

                st.session_state.agregados_temporal = []
                st.rerun()

        st.write("---")
        pago = st.radio("Pago:", ["Efectivo", "Transferencia", "Tarjeta", "Empresa"], horizontal=True)

        if st.button("💰 REGISTRAR PAGO Y LIBERAR"):
            mesa_row = q_one("SELECT * FROM tables_status WHERE mesa=?", (mesa_sel,))
            if not mesa_row:
                st.error("Mesa no encontrada.")
                st.stop()

            exec_sql("""
                INSERT INTO sales(origen,tipo,ubicacion,detalle,detalle_items,total,pago,garzon)
                VALUES(?,?,?,?,?,?,?,?)
            """, (
                mesa_row["origen"] or "🙋‍♂️ Presencial",
                "Local",
                mesa_sel,
                mesa_row["detalle"],
                mesa_row["detalle_items"],
                int(mesa_row["total"]),
                pago,
                mesa_row["garzon"]
            ))

            exec_sql("""
                UPDATE tables_status
                SET estado='Libre', origen='', detalle='', detalle_items='', total=0, garzon='', updated_at=datetime('now')
                WHERE mesa=?
            """, (mesa_sel,))

            st.session_state.mesa_sel = ""
            st.session_state.estado_sel = ""
            st.session_state.agregados_temporal = []
            st.rerun()

    # ==========================
    # Nuevo pedido
    # ==========================
    else:
        st.subheader("🛒 Detalle del Pedido")

        if st.session_state.pedido_temporal:
            for i, it in enumerate(st.session_state.pedido_temporal):
                col_i, col_d = st.columns([5, 1])
                col_i.markdown(
                    f"<div class='item-pedido'>{it['item']} <span>${int(it['precio']):,}</span></div>",
                    unsafe_allow_html=True
                )
                if col_d.button("🗑️", key=f"del_{i}"):
                    st.session_state.pedido_temporal.pop(i)
                    st.rerun()
        else:
            st.info("Aún no hay items. Agrega platos/bebidas o carga desde WhatsApp.")

        with st.expander("➕ Agregar Platos / Bebidas"):
            df_platos, df_bebidas, df_acomp = load_menu_dfs()
            ui_agregar_items("pedido_temporal", df_platos, df_bebidas, df_acomp)

        subtotal = sum(int(i["precio"]) for i in st.session_state.pedido_temporal)
        total_f = subtotal + (delivery_fee if modalidad == "🛵 Entrega" else 0)

        st.markdown(f"<div class='price-tag'>SUBTOTAL: ${int(subtotal):,}</div>", unsafe_allow_html=True)
        if modalidad == "🛵 Entrega":
            st.markdown("<div class='card'>", unsafe_allow_html=True)
            st.write(f"🚚 Delivery: **${int(delivery_fee):,}**")
            st.write(f"🧾 Total: **${int(total_f):,}**")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='price-tag'>TOTAL: ${int(total_f):,}</div>", unsafe_allow_html=True)

        # Preview WhatsApp
        if st.session_state.pedido_temporal:
            msg_preview = build_wa_order_message(
                modalidad=modalidad,
                origen=origen,
                garzon=garzon_turno,
                items=st.session_state.pedido_temporal + ([{"item": "Delivery", "precio": int(delivery_fee), "type": "fee"}] if modalidad == "🛵 Entrega" else []),
                total=int(total_f),
                mesa=st.session_state.mesa_sel if modalidad == "🏠 Local" else "",
                direccion=direccion if modalidad == "🛵 Entrega" else "",
                delivery_fee=int(delivery_fee) if modalidad == "🛵 Entrega" else 0,
            )
            st.markdown(f"[📲 Enviar a cocina por WhatsApp (preview)]({wa_link(num_ws, msg_preview)})")

        if st.button("🚀 CONFIRMAR PEDIDO (DESCUENTA STOCK)"):
            if not st.session_state.pedido_temporal:
                st.warning("No puedes confirmar un pedido vacío.")
                st.stop()
            if modalidad == "🛵 Entrega" and not str(direccion).strip():
                st.warning("Falta la dirección para la entrega.")
                st.stop()

            items_to_save = list(st.session_state.pedido_temporal)
            if modalidad == "🛵 Entrega":
                items_to_save.append({"item": "Delivery", "precio": int(delivery_fee), "type": "fee"})

            ok, msg = stock_apply_sqlite(items_to_save, mode="debit")
            if not ok:
                st.error(msg)
                st.stop()

            res = " / ".join([i["item"] for i in items_to_save])

            # WhatsApp final (incluye mesa)
            msg_final = build_wa_order_message(
                modalidad=modalidad,
                origen=origen,
                garzon=garzon_turno,
                items=items_to_save,
                total=int(total_f),
                mesa=st.session_state.mesa_sel if modalidad == "🏠 Local" else "",
                direccion=direccion if modalidad == "🛵 Entrega" else "",
                delivery_fee=int(delivery_fee) if modalidad == "🛵 Entrega" else 0,
            )
            st.session_state.last_wa_msg = msg_final
            st.session_state.last_wa_link = wa_link(num_ws, msg_final)

            if modalidad == "🏠 Local":
                mesa_sel = st.session_state.mesa_sel
                exec_sql("""
                    UPDATE tables_status
                    SET estado='Ocupada', origen=?, detalle=?, detalle_items=?, total=?, garzon=?, updated_at=datetime('now')
                    WHERE mesa=?
                """, (
                    origen,
                    res,
                    json.dumps(items_to_save, ensure_ascii=False),
                    int(total_f),
                    garzon_turno,
                    mesa_sel
                ))
            else:
                exec_sql("""
                    INSERT INTO sales(origen,tipo,ubicacion,detalle,detalle_items,total,pago,garzon)
                    VALUES(?,?,?,?,?,?,?,?)
                """, (
                    origen,
                    "Entrega",
                    direccion,
                    res,
                    json.dumps(items_to_save, ensure_ascii=False),
                    int(total_f),
                    "Pendiente",
                    garzon_turno
                ))

            # limpiar
            st.session_state.pedido_temporal = []
            st.session_state.agregados_temporal = []
            st.session_state.mesa_sel = ""
            st.session_state.estado_sel = ""
            st.session_state.direccion_wa = ""
            st.session_state.texto_ws_temp = ""
            st.rerun()


# =========================================================
# 11) ADMIN
# =========================================================
elif perfil == "🔐 Admin":
    if not admin_ok:
        st.warning("Clave incorrecta.")
        st.stop()

    t_rep, t_man = st.tabs(["📊 Reportes y Ventas", "⚙️ Mantenedor / Ajustes"])

    with t_man:
        st.subheader("⚙️ Ajustes")
        c1, c2 = st.columns(2)
        new_fee = c1.number_input("Delivery Fee ($)", min_value=0, value=int(delivery_fee), step=100)
        if c1.button("Guardar Delivery"):
            settings_set("delivery_fee", str(int(new_fee)))
            st.success("Delivery actualizado.")
            st.rerun()

        st.caption("⚠️ Seguridad: cambia ADMIN_PASSWORD con variable de entorno KOKELUCHE_ADMIN_PASS si expones a internet.")

        st.write("---")

        # Garzones
        st.subheader("👥 Garzones")
        garzones = load_garzones_list()
        df_g = pd.DataFrame({"Nombre": garzones})
        e_g = st.data_editor(df_g, num_rows="dynamic", use_container_width=True, key="e_g")

        # Menu mantenedores
        st.subheader("🍴 Platos")
        dfp = pd.DataFrame(q_all("SELECT id, name AS Nombre, price AS Precio, stock AS Stock, active AS Activo FROM menu_items WHERE type='plato'"))
        e_p = st.data_editor(dfp, num_rows="dynamic", use_container_width=True, key="e_p")

        st.subheader("🍟 Acompañamientos / Agregados")
        dfa = pd.DataFrame(q_all("SELECT id, name AS Nombre, price AS Precio, stock AS Stock, active AS Activo FROM menu_items WHERE type='acomp'"))
        e_a = st.data_editor(dfa, num_rows="dynamic", use_container_width=True, key="e_a")

        st.subheader("🥤 Bebidas")
        dfb = pd.DataFrame(q_all("SELECT id, name AS Nombre, price AS Precio, stock AS Stock, active AS Activo FROM menu_items WHERE type='bebida'"))
        e_b = st.data_editor(dfb, num_rows="dynamic", use_container_width=True, key="e_b")

        st.write("---")
        if st.button("💾 GUARDAR TODO"):
            # guardar garzones en settings
            gg = []
            for _, r in e_g.iterrows():
                name = str(r.get("Nombre", "")).strip()
                if name:
                    gg.append(name)
            if not gg:
                gg = ["Garzón 1", "Garzón 2"]
            settings_set("garzones_json", json.dumps(gg, ensure_ascii=False))

            def upsert_menu(df_edit: pd.DataFrame, item_type: str):
                for _, r in df_edit.iterrows():
                    rid = r.get("id", None)
                    nombre = str(r.get("Nombre", "")).strip()
                    if not nombre:
                        continue

                    precio = pd.to_numeric(r.get("Precio", 0), errors="coerce")
                    stock = pd.to_numeric(r.get("Stock", 0), errors="coerce")
                    activo = r.get("Activo", 1)

                    try:
                        precio = int(0 if pd.isna(precio) else precio)
                    except:
                        precio = 0
                    try:
                        stock = int(0 if pd.isna(stock) else stock)
                    except:
                        stock = 0
                    try:
                        activo = int(1 if pd.isna(activo) else activo)
                    except:
                        activo = 1

                    precio = max(0, precio)
                    stock = max(0, stock)
                    activo = 1 if activo else 0

                    if pd.isna(rid) or rid is None or str(rid).strip() == "":
                        # inserta si no existe; si existe por UNIQUE(name), actualiza
                        exec_sql("""
                            INSERT INTO menu_items(type,name,price,stock,active)
                            VALUES(?,?,?,?,?)
                            ON CONFLICT(name) DO UPDATE SET
                                type=excluded.type,
                                price=excluded.price,
                                stock=excluded.stock,
                                active=excluded.active
                        """, (item_type, nombre, precio, stock, activo))
                    else:
                        exec_sql("""
                            UPDATE menu_items
                            SET name=?, price=?, stock=?, active=?, type=?
                            WHERE id=?
                        """, (nombre, precio, stock, activo, item_type, int(rid)))

            # Proteger "❌ SIN ACOMP." para que no se quede sin stock accidentalmente
            if "Nombre" in e_a.columns:
                for i, r in e_a.iterrows():
                    if str(r.get("Nombre", "")).strip() == "❌ SIN ACOMP.":
                        e_a.at[i, "Stock"] = max(int(e_a.at[i, "Stock"]), 999999)
                        e_a.at[i, "Precio"] = 0
                        e_a.at[i, "Activo"] = 1

            upsert_menu(e_p, "plato")
            upsert_menu(e_a, "acomp")
            upsert_menu(e_b, "bebida")

            st.success("Guardado.")
            st.rerun()

    with t_rep:
        st.header("📈 Reportes")
        ventas = load_ventas_df()

        if ventas.empty:
            st.info("Aún no hay ventas registradas.")
        else:
            tmp = ventas.copy()
            tmp["Fecha_dt"] = pd.to_datetime(tmp["Fecha"], errors="coerce")

            c1, c2 = st.columns(2)
            f_origen = c1.multiselect("Filtrar Origen:", sorted([x for x in tmp["Origen"].unique() if str(x).strip()]))
            f_garzon = c2.multiselect("Filtrar Garzón:", sorted([x for x in tmp["Garzon"].unique() if str(x).strip()]))

            if f_origen:
                tmp = tmp[tmp["Origen"].isin(f_origen)]
            if f_garzon:
                tmp = tmp[tmp["Garzon"].isin(f_garzon)]

            st.subheader("Ventas por Garzón")
            rep_g = tmp.groupby("Garzon")["Total"].sum().reset_index().sort_values("Total", ascending=False)
            if not rep_g.empty:
                st.bar_chart(rep_g.set_index("Garzon"))

            st.subheader("Ventas por Origen")
            rep_o = tmp.groupby("Origen")["Total"].sum().reset_index().sort_values("Total", ascending=False)
            if not rep_o.empty:
                st.bar_chart(rep_o.set_index("Origen"))

            st.subheader("Detalle de ventas")
            st.dataframe(tmp.drop(columns=["Fecha_dt"]), use_container_width=True)
