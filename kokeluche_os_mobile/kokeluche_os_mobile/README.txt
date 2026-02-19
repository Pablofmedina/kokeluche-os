Kokeluche OS v44 (Streamlit) - Uso desde celular

OPCIÓN A (rápida): usar desde el celu en la misma Wi‑Fi (recomendado)
1) En el PC (Windows):
   - Instala Python 3.10+.
   - Abre CMD en esta carpeta y ejecuta:
       python -m venv venv
       venv\Scripts\activate
       pip install -r requirements.txt
       streamlit run app.py --server.address 0.0.0.0 --server.port 8501

2) Permite el puerto en el Firewall:
   - Cuando Windows pregunte, marca "Redes privadas".

3) Averigua la IP del PC:
   - En CMD: ipconfig
   - Busca "IPv4 Address" (ej: 192.168.1.20)

4) En el celular (mismo Wi‑Fi):
   - Abre Chrome/Safari y entra a:
       http://IP_DEL_PC:8501
     Ejemplo:
       http://192.168.1.20:8501

5) Para que quede como "app":
   - Android/Chrome: menú (⋮) -> "Agregar a pantalla principal"
   - iPhone/Safari: compartir -> "Agregar a inicio"

OPCIÓN B (acceso desde cualquier lugar):
- Súbelo a un hosting (ej: Streamlit Community Cloud / Render / Railway).
- En ese caso conviene cambiar la clave de Admin y pensar en base de datos (SQLite).

Notas:
- Si varios garzones usan a la vez, el sistema usa CSV. Funciona, pero no es lo ideal. Para uso intensivo conviene migrar a SQLite.
- Logo: si tienes logo.png ponlo en la misma carpeta que app.py
