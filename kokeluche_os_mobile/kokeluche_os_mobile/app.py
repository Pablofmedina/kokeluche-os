import streamlit as st
import pandas as pd
import os
from datetime import datetime
import urllib.parse
import re
import unicodedata
import json
import html as html_lib

# =========================================================
# 0) CONFIG GENERAL
# =========================================================
APP_TITLE = "Kokeluche OS v44"
FOLDER = "data"
LOGO_PATH = "logo.png"
DELIVERY_FEE = 500
ADMIN_PASSWORD = os.getenv("KOKELUCHE_ADMIN_PASS", "1234")

os.makedirs(FOLDER, exist_ok=True)

# =========================================================
# 1) HELPERS
# =========================================================
def normalize_text(s: str) -> str:
    s = "" if s is None else str(s)
    s = s.strip().lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"\s+", " ", s)
    return s

def cargar(file: str, cols: list[str]) -> pd.DataFrame:
    path = os.path.join(FOLDER, file)
    if not os.path.exists(path):
        return pd.DataFrame(columns=cols)

    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=cols)

    # agrega columnas faltantes (migración suave)
    for col in cols:
        if col not in df.columns:
            if col == "Stock":
                df[col] = 9999  # para no dejar todo sin stock al migrar
            else:
                df[col] = 0 if ("Precio" in col or "Total" in col) else ""

    # normaliza tipos
    for col in cols:
        if col in ("Stock",) or ("Precio" in col) or ("Total" in col):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
        else:
            df[col] = df[col].fillna("").astype(str)

    return df[cols]

def guardar(df: pd.DataFrame, file: str) -> None:
    path = os.path.join(FOLDER, file)
    tmp = path + ".tmp"
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def wa_link(numero: str, mensaje: str) -> str:
    n = re.sub(r"\D", "", str(numero))
    return f"https://wa.me/{n}?text={urllib.parse.quote(mensaje)}"

def extraer_direccion(texto: str) -> str:
    t = texto.strip()
    m = re.search(r"(?:direcci[oó]n)\s*[:\-]\s*(.+)$", t, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:entrega|entregar|env[ií]o)\s+(?:a|en)\s+(.+)$", t, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""

def safe_load_items(s: str):
    """Lee DetalleItems (JSON) si existe. Retorna list[dict] o None."""
    try:
        if not s or str(s).strip() == "":
            return None
        data = json.loads(s)
        if isinstance(data, list):
            out = []
            for it in data:
                if isinstance(it, dict):
                    if "item" in it and "precio" in it:
                        out.append(it)
            return out
        return None
    except Exception:
        return None

def build_ticket_text(fecha_str, mesa, garzon, items, subtotal):
    propina = int(round(subtotal * 0.10))
    total_con_propina = int(subtotal + propina)

    lines = []
    lines.append("🧾 KOKELUCHE TICKET")
    lines.append(f"Fecha: {fecha_str}")
    lines.append(f"Mesa: {mesa}")
    lines.append(f"Atiende: {garzon}")
    lines.append("-" * 28)

    if items:
        for it in items:
            precio = int(it.get("precio", 0))
            label = str(it.get("item", ""))
            lines.append(f"- {label}  ${precio:,}")
    else:
        lines.append("- (sin desglose guardado)")

    lines.append("-" * 28)
    lines.append(f"SUBTOTAL:             ${int(subtotal):,}")
    lines.append(f"PROPINA SUG. (10%):   ${int(propina):,}")
    lines.append(f"TOTAL + PROPINA:      ${int(total_con_propina):,}")
    return "\n".join(lines), propina, total_con_propina

# =========================================================
# 2) STOCK: VALIDAR + DESCONTAR / REPONER
# =========================================================
def items_to_needs(items: list[dict]) -> tuple[dict, dict, dict]:
    """
    Devuelve necesidades:
    - need_platos[Plato] = qty
    - need_acomp[Acomp] = qty
    - need_bebidas[Producto] = qty
    Solo cuenta lo estructurado. Si viene antiguo (solo item/precio), no descuenta (no se puede inferir).
    """
    need_pl, need_ac, need_be = {}, {}, {}

    for it in items:
        t = it.get("type", "")
        qty = int(it.get("qty", 1))

        if t == "plato":
            pl = it.get("plato", "")
            if pl:
                need_pl[pl] = need_pl.get(pl, 0) + qty
            ac = it.get("acomp", "")
            if ac and ac != "❌ SIN ACOMP.":
                need_ac[ac] = need_ac.get(ac, 0) + qty

        elif t == "acomp":
            ac = it.get("acomp", "")
            if ac and ac != "❌ SIN ACOMP.":
                need_ac[ac] = need_ac.get(ac, 0) + qty

        elif t == "bebida":
            be = it.get("bebida", "")
            if be:
                need_be[be] = need_be.get(be, 0) + qty

    return need_pl, need_ac, need_be

def stock_apply(items: list[dict], mode: str) -> tuple[bool, str]:
    """
    mode:
      - "debit": valida stock >= needed y descuenta
      - "credit": repone (suma stock) sin validar
    """
    dfp = cargar("platos.csv", ["Plato", "Precio", "Stock"])
    dfa = cargar("acompanamientos.csv", ["Acompañamiento", "Precio", "Stock"])
    dfb = cargar("bebidas.csv", ["Producto", "Precio", "Stock"])

    need_pl, need_ac, need_be = items_to_needs(items)

    if mode == "debit":
        for pl, q in need_pl.items():
            if pl not in dfp["Plato"].values:
                return False, f"Plato no existe en menú: {pl}"
            st_pl = int(dfp.loc[dfp["Plato"] == pl, "Stock"].values[0])
            if st_pl < q:
                return False, f"No hay stock suficiente de '{pl}'. Stock: {st_pl}, requerido: {q}"

        for ac, q in need_ac.items():
            if ac not in dfa["Acompañamiento"].values:
                return False, f"Acompañamiento no existe: {ac}"
            st_ac = int(dfa.loc[dfa["Acompañamiento"] == ac, "Stock"].values[0])
            if st_ac < q:
                return False, f"No hay stock suficiente de '{ac}'. Stock: {st_ac}, requerido: {q}"

        for be, q in need_be.items():
            if be not in dfb["Producto"].values:
                return False, f"Bebida no existe: {be}"
            st_be = int(dfb.loc[dfb["Producto"] == be, "Stock"].values[0])
            if st_be < q:
                return False, f"No hay stock suficiente de '{be}'. Stock: {st_be}, requerido: {q}"

    sign = -1 if mode == "debit" else +1

    for pl, q in need_pl.items():
        dfp.loc[dfp["Plato"] == pl, "Stock"] = dfp.loc[dfp["Plato"] == pl, "Stock"].astype(int) + (sign * int(q))

    for ac, q in need_ac.items():
        dfa.loc[dfa["Acompañamiento"] == ac, "Stock"] = dfa.loc[dfa["Acompañamiento"] == ac, "Stock"].astype(int) + (sign * int(q))

    for be, q in need_be.items():
        dfb.loc[dfb["Producto"] == be, "Stock"] = dfb.loc[dfb["Producto"] == be, "Stock"].astype(int) + (sign * int(q))

    dfp["Stock"] = dfp["Stock"].clip(lower=0)
    dfa["Stock"] = dfa["Stock"].clip(lower=0)
    dfb["Stock"] = dfb["Stock"].clip(lower=0)

    guardar(dfp, "platos.csv")
    guardar(dfa, "acompanamientos.csv")
    guardar(dfb, "bebidas.csv")
    return True, ""

# =========================================================
# 3) SESSION STATE
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
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# =========================================================
# 4) DATA (CON STOCK)
# =========================================================
ventas_cols = ["Fecha", "Origen", "Tipo", "Ubicacion", "Detalle", "DetalleItems", "Total", "Pago", "Garzon"]
platos_cols = ["Plato", "Precio", "Stock"]
acomp_cols = ["Acompañamiento", "Precio", "Stock"]
bebidas_cols = ["Producto", "Precio", "Stock"]
garzones_cols = ["Nombre"]
mesas_cols = ["Mesa", "Estado", "Origen", "Detalle", "DetalleItems", "Total", "Garzon"]

ventas = cargar("ventas.csv", ventas_cols)
df_platos = cargar("platos.csv", platos_cols)
df_acomp = cargar("acompanamientos.csv", acomp_cols)
df_bebidas = cargar("bebidas.csv", bebidas_cols)
df_garzones = cargar("garzones.csv", garzones_cols)
mesas_status = cargar("mesas_status.csv", mesas_cols)

if df_garzones.empty:
    df_garzones = pd.DataFrame({"Nombre": ["Garzón 1", "Garzón 2"]})

if df_platos.empty:
    df_platos = pd.DataFrame({"Plato": ["Pollo asado"], "Precio": [5500], "Stock": [30]})
    guardar(df_platos, "platos.csv")

if df_bebidas.empty:
    df_bebidas = pd.DataFrame({"Producto": ["Jugo Piña"], "Precio": [2500], "Stock": [30]})
    guardar(df_bebidas, "bebidas.csv")

if df_acomp.empty:
    df_acomp = pd.DataFrame({"Acompañamiento": ["Papas fritas", "❌ SIN ACOMP."], "Precio": [0, 0], "Stock": [30, 9999]})
    guardar(df_acomp, "acompanamientos.csv")

if mesas_status.empty:
    mesas_status = pd.DataFrame({
        "Mesa": [f"Mesa {i}" for i in range(1, 9)],
        "Estado": ["Libre"] * 8,
        "Origen": [""] * 8,
        "Detalle": [""] * 8,
        "DetalleItems": [""] * 8,
        "Total": [0] * 8,
        "Garzon": [""] * 8,
    })
    guardar(mesas_status, "mesas_status.csv")

# =========================================================
# 5) LECTOR WHATSAPP (ESTRUCTURADO)
# =========================================================
def lector_inteligente():
    texto_raw = st.session_state.get("ws_input", "")
    texto = normalize_text(texto_raw)
    if not texto:
        return

    encontrados = []

    dir_encontrada = extraer_direccion(texto_raw)
    if dir_encontrada:
        st.session_state.direccion_wa = dir_encontrada

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

        ac_en = "❌ SIN ACOMP."
        pr_ac = 0
        for _, row_ac in df_acomp.iterrows():
            ac_nom = str(row_ac["Acompañamiento"])
            ac_n = normalize_text(ac_nom)
            if ac_n and ac_n != normalize_text("❌ SIN ACOMP.") and ac_n in texto:
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
# 6) UI CONFIG + ESTILOS
# =========================================================
st.set_page_config(page_title=APP_TITLE, layout="wide")

st.markdown(
    """
    <style>
    .stApp { background-color: #f8f9fa; }
    .stButton>button { border-radius: 12px; font-weight: 700; width: 100%; height: 3.2em; }
    .card { background-color: white; padding: 20px; border-radius: 15px; box-shadow: 0px 4px 12px rgba(0,0,0,0.05); margin-bottom: 20px; border: 1px solid #eee; }
    .price-tag { background-color: #fff1f2; color: #E63946; padding: 15px; border-radius: 12px; font-weight: 800; text-align: center; font-size: 28px; border: 2px solid #E63946; }
    .item-pedido { background-color: #f1f3f5; padding: 8px 10px; border-radius: 8px; margin-bottom: 6px; border-left: 5px solid #25d366; display: flex; justify-content: space-between; font-weight: 700; }
    .ticket-box { background-color: #fff; border: 1px dashed #000; padding: 16px; font-family: 'Courier New', Courier, monospace; color: #000; }
    .info { background-color: #e3f2fd; padding: 10px; border-radius: 8px; margin-bottom: 10px; color: #0d47a1; font-weight: 700; }
    .hint { color:#6c757d; font-size: 12px; }
    </style>
    """,
    unsafe_allow_html=True
)

# =========================================================
# 7) SIDEBAR
# =========================================================
with st.sidebar:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH)

    perfil = st.radio("PERFIL:", ["👨‍🍳 Garzón", "🔐 Admin"])
    admin_ok = False
    if perfil == "🔐 Admin":
        admin_ok = (st.text_input("Clave", type="password") == ADMIN_PASSWORD)

    num_ws = st.text_input("WhatsApp Cocina:", "56989803518")

    if st.session_state.last_wa_link:
        st.success("Pedido registrado.")
        st.markdown(f"[📲 Enviar a cocina por WhatsApp]({st.session_state.last_wa_link})")
        if st.button("✅ Cerrar aviso"):
            st.session_state.last_wa_link = ""
            st.session_state.last_wa_msg = ""

# =========================================================
# 8) COMPONENTE: AGREGAR ITEMS (CON STOCK)
# =========================================================
def ui_agregar_items(lista_key: str):
    st.session_state.setdefault(lista_key, [])

    tipo_p = st.radio("Producto:", ["Plato Fondo", "Bebida/Jugo", "Acompañamiento solo"], horizontal=True, key=f"tp_{lista_key}")

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
# 9) CAPA GARZÓN
# =========================================================
if perfil == "👨‍🍳 Garzón":
    st.title("🍴 Panel de Ventas")

    st.markdown("<div class='card'>", unsafe_allow_html=True)
    garzon_turno = st.selectbox("👤 Atendido por:", df_garzones["Nombre"])
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
        if st.button("🔍 CARGAR PEDIDO DE WHATSAPP", on_click=lector_inteligente):
            st.success("Detectado.")
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

    if modalidad == "🏠 Local" and st.session_state.estado_sel == "Ocupada":
        idx = mesas_status[mesas_status["Mesa"] == st.session_state.mesa_sel].index[0]

        st.markdown(
            f"<div class='info'>Mesa atendida por: {mesas_status.at[idx, 'Garzon']} | Origen: {mesas_status.at[idx, 'Origen'] or '-'}</div>",
            unsafe_allow_html=True
        )

        total_c = int(mesas_status.at[idx, "Total"])
        propina_sug = int(round(total_c * 0.10))
        total_con_propina = int(total_c + propina_sug)

        st.markdown(f"<div class='price-tag'>SUBTOTAL MESA: ${total_c:,}</div>", unsafe_allow_html=True)
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.write(f"💡 Propina sugerida (10%): **${propina_sug:,}**")
        st.write(f"🧾 Total con propina: **${total_con_propina:,}**")
        st.markdown("</div>", unsafe_allow_html=True)

        t_col1, t_col2, t_col3 = st.columns(3)

        if t_col1.button("📄 GENERAR TICKET (IMPRIMIR)"):
            items = safe_load_items(mesas_status.at[idx, "DetalleItems"])
            fecha_str = datetime.now().strftime("%d/%m/%Y %H:%M")
            ticket_text, _, _ = build_ticket_text(
                fecha_str=fecha_str,
                mesa=st.session_state.mesa_sel,
                garzon=mesas_status.at[idx, "Garzon"],
                items=items,
                subtotal=total_c
            )
            st.markdown(
                f"<div class='ticket-box'><pre>{html_lib.escape(ticket_text)}</pre></div>",
                unsafe_allow_html=True
            )

        if t_col2.button("🚫 ANULAR MESA (REPONE STOCK)"):
            items = safe_load_items(mesas_status.at[idx, "DetalleItems"]) or []
            ok, msg = stock_apply(items, mode="credit")
            if not ok:
                st.error(msg)
                st.stop()

            mesas_status.at[idx, "Estado"] = "Libre"
            mesas_status.at[idx, "Origen"] = ""
            mesas_status.at[idx, "Detalle"] = ""
            mesas_status.at[idx, "DetalleItems"] = ""
            mesas_status.at[idx, "Total"] = 0
            mesas_status.at[idx, "Garzon"] = ""
            guardar(mesas_status, "mesas_status.csv")
            st.session_state.mesa_sel = ""
            st.session_state.estado_sel = ""
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
            ui_agregar_items("agregados_temporal")

        add_total = sum(int(i["precio"]) for i in st.session_state.agregados_temporal)
        if add_total > 0:
            st.markdown(f"<div class='price-tag'>AGREGADOS: ${add_total:,}</div>", unsafe_allow_html=True)

            if st.button("✅ CONFIRMAR AGREGADOS A LA MESA (DESCUENTA STOCK)"):
                ok, msg = stock_apply(st.session_state.agregados_temporal, mode="debit")
                if not ok:
                    st.error(msg)
                    st.stop()

                det_ant = str(mesas_status.at[idx, "Detalle"]).strip()
                det_new = " / ".join([x["item"] for x in st.session_state.agregados_temporal])
                det_final = (det_ant + " / " + det_new) if det_ant else det_new

                old_items = safe_load_items(mesas_status.at[idx, "DetalleItems"]) or []
                new_items = old_items + list(st.session_state.agregados_temporal)

                mesas_status.at[idx, "Detalle"] = det_final
                mesas_status.at[idx, "DetalleItems"] = json.dumps(new_items, ensure_ascii=False)
                mesas_status.at[idx, "Total"] = int(mesas_status.at[idx, "Total"]) + int(add_total)

                guardar(mesas_status, "mesas_status.csv")
                st.session_state.agregados_temporal = []
                st.rerun()

        st.write("---")
        pago = st.radio("Pago:", ["Efectivo", "Transferencia", "Tarjeta", "Empresa"], horizontal=True)

        if st.button("💰 REGISTRAR PAGO Y LIBERAR"):
            nv = pd.DataFrame([{
                "Fecha": now_iso(),
                "Origen": mesas_status.at[idx, "Origen"] or "🙋‍♂️ Presencial",
                "Tipo": "Local",
                "Ubicacion": st.session_state.mesa_sel,
                "Detalle": mesas_status.at[idx, "Detalle"],
                "DetalleItems": mesas_status.at[idx, "DetalleItems"],
                "Total": int(mesas_status.at[idx, "Total"]),
                "Pago": pago,
                "Garzon": mesas_status.at[idx, "Garzon"],
            }])
            ventas = pd.concat([ventas, nv], ignore_index=True)
            guardar(ventas, "ventas.csv")

            mesas_status.at[idx, "Estado"] = "Libre"
            mesas_status.at[idx, "Origen"] = ""
            mesas_status.at[idx, "Detalle"] = ""
            mesas_status.at[idx, "DetalleItems"] = ""
            mesas_status.at[idx, "Total"] = 0
            mesas_status.at[idx, "Garzon"] = ""
            guardar(mesas_status, "mesas_status.csv")

            st.session_state.mesa_sel = ""
            st.session_state.estado_sel = ""
            st.rerun()

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
            ui_agregar_items("pedido_temporal")

        subtotal = sum(int(i["precio"]) for i in st.session_state.pedido_temporal)
        total_f = subtotal + (DELIVERY_FEE if modalidad == "🛵 Entrega" else 0)

        st.markdown(f"<div class='price-tag'>SUBTOTAL: ${int(subtotal):,}</div>", unsafe_allow_html=True)
        if modalidad == "🛵 Entrega":
            st.markdown("<div class='card'>", unsafe_allow_html=True)
            st.write(f"🚚 Delivery: **${DELIVERY_FEE:,}**")
            st.write(f"🧾 Total: **${int(total_f):,}**")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div class='price-tag'>TOTAL: ${int(total_f):,}</div>", unsafe_allow_html=True)

        if st.session_state.pedido_temporal:
            res_preview = " / ".join([i["item"] for i in st.session_state.pedido_temporal])
            msg = (
                f"🍴 PEDIDO {('ENTREGA' if modalidad=='🛵 Entrega' else 'LOCAL')}\n"
                f"👤 Garzón: {garzon_turno}\n"
                f"🕒 {datetime.now().strftime('%H:%M')}\n"
                f"📦 Detalle: {res_preview}\n"
                f"💰 Total: ${int(total_f):,}\n"
            )
            if modalidad == "🛵 Entrega":
                msg += f"📍 Dirección: {direccion or '(falta)'}\n"
                msg += f"🚚 Delivery: ${DELIVERY_FEE:,}\n"
            st.markdown(f"[📲 Enviar a cocina por WhatsApp (preview)]({wa_link(num_ws, msg)})")

        if st.button("🚀 CONFIRMAR PEDIDO (DESCUENTA STOCK)"):
            if not st.session_state.pedido_temporal:
                st.warning("No puedes confirmar un pedido vacío.")
                st.stop()
            if modalidad == "🛵 Entrega" and not str(direccion).strip():
                st.warning("Falta la dirección para la entrega.")
                st.stop()

            items_to_save = list(st.session_state.pedido_temporal)
            if modalidad == "🛵 Entrega":
                items_to_save.append({"item": "Delivery", "precio": int(DELIVERY_FEE), "type": "fee"})

            ok, msg = stock_apply(items_to_save, mode="debit")
            if not ok:
                st.error(msg)
                st.stop()

            res = " / ".join([i["item"] for i in items_to_save])

            msg_final = (
                f"🍴 PEDIDO {('ENTREGA' if modalidad=='🛵 Entrega' else 'LOCAL')}\n"
                f"👤 Garzón: {garzon_turno}\n"
                f"🕒 {datetime.now().strftime('%H:%M')}\n"
                f"📦 Detalle: {res}\n"
                f"💰 Total: ${int(total_f):,}\n"
            )
            if modalidad == "🛵 Entrega":
                msg_final += f"📍 Dirección: {direccion}\n"
                msg_final += f"🚚 Delivery: ${DELIVERY_FEE:,}\n"

            st.session_state.last_wa_msg = msg_final
            st.session_state.last_wa_link = wa_link(num_ws, msg_final)

            if modalidad == "🏠 Local":
                idx = mesas_status[mesas_status["Mesa"] == st.session_state.mesa_sel].index[0]
                mesas_status.at[idx, "Estado"] = "Ocupada"
                mesas_status.at[idx, "Origen"] = origen
                mesas_status.at[idx, "Detalle"] = res
                mesas_status.at[idx, "DetalleItems"] = json.dumps(items_to_save, ensure_ascii=False)
                mesas_status.at[idx, "Total"] = int(total_f)
                mesas_status.at[idx, "Garzon"] = garzon_turno
                guardar(mesas_status, "mesas_status.csv")
            else:
                nv = pd.DataFrame([{
                    "Fecha": now_iso(),
                    "Origen": origen,
                    "Tipo": "Entrega",
                    "Ubicacion": direccion,
                    "Detalle": res,
                    "DetalleItems": json.dumps(items_to_save, ensure_ascii=False),
                    "Total": int(total_f),
                    "Pago": "Pendiente",
                    "Garzon": garzon_turno
                }])
                ventas = pd.concat([ventas, nv], ignore_index=True)
                guardar(ventas, "ventas.csv")

            st.session_state.pedido_temporal = []
            st.session_state.agregados_temporal = []
            st.session_state.mesa_sel = ""
            st.session_state.estado_sel = ""
            st.session_state.direccion_wa = ""
            st.session_state.texto_ws_temp = ""
            st.rerun()

# =========================================================
# 10) CAPA ADMIN
# =========================================================
elif perfil == "🔐 Admin" and admin_ok:
    t_rep, t_man = st.tabs(["📊 Reportes y Ventas", "⚙️ Mantenedor de Menú"])

    with t_man:
        st.markdown("<div class='card'>", unsafe_allow_html=True)
        st.write("🧠 **Inventario (Stock):** coloca la cantidad preparada/disponible. Si llega a 0, el sistema lo marca como **NO DISPONIBLE** automáticamente.")
        st.markdown("</div>", unsafe_allow_html=True)

        st.subheader("👥 Mantenedor de Garzones")
        e_g = st.data_editor(df_garzones, num_rows="dynamic", use_container_width=True, key="e_g")

        st.subheader("🍴 Mantenedor de Platos, Precios y Stock")
        e_p = st.data_editor(df_platos, num_rows="dynamic", use_container_width=True, key="e_p")

        st.subheader("🍟 Mantenedor de Acompañamientos (Agregados), Precios y Stock")
        e_a = st.data_editor(df_acomp, num_rows="dynamic", use_container_width=True, key="e_a")

        st.subheader("🥤 Mantenedor de Jugos y Bebidas, Precios y Stock")
        e_b = st.data_editor(df_bebidas, num_rows="dynamic", use_container_width=True, key="e_b")

        if st.button("💾 GUARDAR TODOS LOS CAMBIOS"):
            for df_ in (e_p, e_a, e_b):
                if "Stock" in df_.columns:
                    df_["Stock"] = pd.to_numeric(df_["Stock"], errors="coerce").fillna(0).astype(int).clip(lower=0)

            guardar(e_g, "garzones.csv")
            guardar(e_p, "platos.csv")
            guardar(e_a, "acompanamientos.csv")
            guardar(e_b, "bebidas.csv")
            st.success("Guardado.")
            st.rerun()

    with t_rep:
        st.header("📈 Reportes")

        if ventas.empty:
            st.info("Aún no hay ventas registradas.")
        else:
            tmp = ventas.copy()
            tmp["Fecha_dt"] = pd.to_datetime(tmp["Fecha"], errors="coerce")

            c1, c2 = st.columns(2)
            f_origen = c1.multiselect("Filtrar Origen:", sorted(tmp["Origen"].unique()))
            f_garzon = c2.multiselect("Filtrar Garzón:", sorted(tmp["Garzon"].unique()))

            if f_origen:
                tmp = tmp[tmp["Origen"].isin(f_origen)]
            if f_garzon:
                tmp = tmp[tmp["Garzon"].isin(f_garzon)]

            st.subheader("Ventas por Garzón")
            rep_g = tmp.groupby("Garzon")["Total"].sum().reset_index().sort_values("Total", ascending=False)
            st.bar_chart(rep_g.set_index("Garzon"))

            st.subheader("Ventas por Origen")
            rep_o = tmp.groupby("Origen")["Total"].sum().reset_index().sort_values("Total", ascending=False)
            st.bar_chart(rep_o.set_index("Origen"))

            st.subheader("Detalle de ventas")
            st.dataframe(tmp.drop(columns=["Fecha_dt"]), use_container_width=True)

else:
    if perfil == "🔐 Admin" and not admin_ok:
        st.warning("Clave incorrecta.")
