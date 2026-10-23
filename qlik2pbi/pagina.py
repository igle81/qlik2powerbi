"""HTML de la web. No enlaza recursos externos."""

from __future__ import annotations

import html

_ESTILO = """
    :root { color-scheme: light; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Helvetica Neue", sans-serif;
      background: #f3efe6;
      color: #1c1915;
    }
    header {
      background: #163528;
      color: #f6f3ec;
      padding: 28px 20px 22px;
    }
    header h1 { margin: 0 0 8px; font-size: 1.7rem; font-weight: 650; }
    header p { margin: 0; max-width: 42rem; line-height: 1.45; color: #d7e4dc; }
    main { max-width: 40rem; margin: 0 auto; padding: 24px 16px 48px; }
    form {
      background: #fffdf8;
      border: 1px solid #e2d9c8;
      border-radius: 12px;
      padding: 20px;
      display: grid;
      gap: 16px;
    }
    label { display: grid; gap: 6px; font-weight: 600; }
    input { font-weight: 400; font: inherit; padding: 8px; }
    button {
      background: #163528;
      color: white;
      border: 0;
      border-radius: 8px;
      padding: 12px 16px;
      font-size: 1rem;
      cursor: pointer;
    }
    button:disabled { opacity: 0.6; cursor: wait; }
    #estado, .aviso { min-height: 1.4em; margin: 0; }
    .aviso { color: #8a2b1b; }
    footer { color: #5c564c; font-size: 0.92rem; line-height: 1.45; }
"""


def html_acceso(mensaje: str = "", cerrada: bool = False) -> str:
    if cerrada:
        cuerpo = """
        <form>
          <p class="aviso">El acceso está cerrado.</p>
        </form>
        <footer><p>Esta página no está disponible.</p></footer>
        """
    else:
        aviso = f'<p class="aviso" role="alert">{html.escape(mensaje)}</p>' if mensaje else ""
        cuerpo = f"""
        <form method="post" action="/api/acceso" autocomplete="off">
          <label>Código de acceso
            <input name="codigo" type="password" autocomplete="current-password" spellcheck="false" required>
          </label>
          <button type="submit">Entrar</button>
          {aviso}
        </form>
        <footer><p>Sin el código no se pueden subir ficheros ni descargar el proyecto.</p></footer>
        """
    resumen = (
        "El acceso está cerrado."
        if cerrada
        else "Escribe el código de acceso. Los ficheros se convierten en este servidor y no salen a ningún otro servicio."
    )
    return _documento(resumen, cuerpo)


def html_convertidor() -> str:
    cuerpo = """
    <form id="formulario">
      <label>Script de carga (.qvs)
        <input id="script" name="script" type="file" accept=".qvs,.txt,.qlik" required>
      </label>
      <label>Exportación de la app (.json, .xml o .qvf)
        <input id="export" name="export" type="file" accept=".json,.xml,.qvf" required>
      </label>
      <button type="submit">Generar y descargar ZIP</button>
      <p id="estado" role="status"></p>
    </form>
    <footer>
      <p>La sesión dura 12 horas en este navegador. Cada fichero puede pesar hasta 4 MB. El ZIP incluye el modelo, las medidas, las páginas y un INFORME.md. No se guarda nada en el servidor.</p>
    </footer>
    <script>
      const form = document.getElementById("formulario");
      const estado = document.getElementById("estado");
      const boton = form.querySelector("button");
      form.addEventListener("submit", async (evento) => {
        evento.preventDefault();
        estado.className = "";
        estado.textContent = "Generando el proyecto…";
        boton.disabled = true;
        const datos = new FormData();
        datos.append("script", document.getElementById("script").files[0]);
        datos.append("export", document.getElementById("export").files[0]);
        try {
          const respuesta = await fetch("/api/convert", { method: "POST", body: datos });
          if (!respuesta.ok) {
            let mensaje = "No se ha podido generar el proyecto.";
            try {
              const cuerpo = await respuesta.json();
              if (cuerpo.error) mensaje = cuerpo.error;
            } catch (e) { /* el cuerpo no era JSON */ }
            throw new Error(mensaje);
          }
          const blob = await respuesta.blob();
          const avisos = respuesta.headers.get("X-Avisos");
          const dispo = respuesta.headers.get("Content-Disposition") || "";
          const coincidencia = dispo.match(/filename="([^"]+)"/);
          const nombre = coincidencia ? coincidencia[1] : "qlik2pbi.zip";
          const url = URL.createObjectURL(blob);
          const enlace = document.createElement("a");
          enlace.href = url;
          enlace.download = nombre;
          enlace.click();
          URL.revokeObjectURL(url);
          estado.textContent = avisos && avisos !== "0"
            ? "ZIP descargado. El INFORME.md del paquete lista " + avisos + " avisos para revisar."
            : "ZIP descargado. Ábrelo en Power BI Desktop.";
        } catch (error) {
          estado.className = "aviso";
          estado.textContent = error.message;
        } finally {
          boton.disabled = false;
        }
      });
    </script>
    """
    return _documento(
        "Sube el script de carga y la exportación de la app. El proyecto se genera en memoria.",
        cuerpo,
    )


def _documento(resumen: str, cuerpo: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <meta name="googlebot" content="noindex, nofollow">
  <title>De Qlik a Power BI</title>
  <style>{_ESTILO}</style>
</head>
<body>
  <header>
    <h1>De Qlik a Power BI</h1>
    <p>{resumen}</p>
  </header>
  <main>
    {cuerpo}
  </main>
</body>
</html>
"""
