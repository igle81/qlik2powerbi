"""HTML de la web. No enlaza recursos externos."""

from __future__ import annotations

import html
import json

from qlik2pbi.qvf import JS_EXTRAER, MENSAJE_QVF_DANADO, SOBRE

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
    #estado, .aviso { min-height: 1.4em; margin: 0; white-space: pre-wrap; }
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
    mensaje = json.dumps(MENSAJE_QVF_DANADO, ensure_ascii=False)
    sobre = json.dumps(SOBRE)
    cuerpo = f"""
    <form id="formulario">
      <label>Script de carga (.qvs)
        <input id="script" name="script" type="file" accept=".qvs,.txt" required>
      </label>
      <label>App de Qlik Sense (.qvf)
        <input id="qvf" name="qvf" type="file" accept=".qvf" required>
      </label>
      <button type="submit">Generar y descargar ZIP</button>
      <p id="estado" role="status"></p>
    </form>
    <footer>
      <p>La sesión dura 12 horas en este navegador. El .qvs puede pesar hasta 4 MB. El .qvf se lee en este navegador y solo se envían las hojas, los objetos y las medidas, no las filas de datos. El ZIP incluye el modelo, las medidas, las páginas y un INFORME.md. No se guarda nada en el servidor.</p>
    </footer>
    <script>
      __JS_EXTRAER__
      const form = document.getElementById("formulario");
      const estado = document.getElementById("estado");
      const boton = form.querySelector("button");
      const MENSAJE_QVF = {mensaje};
      const TOPE = 4 * 1024 * 1024;
      form.addEventListener("submit", async (evento) => {{
        evento.preventDefault();
        estado.className = "";
        estado.textContent = "Leyendo la app…";
        boton.disabled = true;
        try {{
          const script = document.getElementById("script").files[0];
          const qvf = document.getElementById("qvf").files[0];
          if (!script || !qvf) throw new Error("Hay que subir el script de carga (.qvs) y la app (.qvf).");
          const nombre = (qvf.name || "").toLowerCase();
          if (!nombre.endsWith(".qvf")) throw new Error("La app tiene que ser un fichero .qvf de Qlik Sense.");
          let objetos;
          try {{
            objetos = await extraerObjetosQvf(new Uint8Array(await qvf.arrayBuffer()));
          }} catch (error) {{
            throw new Error(MENSAJE_QVF);
          }}
          if (!objetos.length) throw new Error(MENSAJE_QVF);
          const sobre = JSON.stringify({{
            qlik2pbi: {sobre},
            fichero: qvf.name,
            objetos: objetos.map(recortar)
          }});
          if (sobre.length > TOPE) {{
            throw new Error("Los metadatos de esta app superan 4 MB. Usa la línea de comandos, que lee el .qvf en local.");
          }}
          estado.textContent = "Generando el proyecto…";
          const datos = new FormData();
          datos.append("script", script);
          datos.append("export", new Blob([sobre], {{ type: "application/json" }}), qvf.name + ".metadatos.json");
          const respuesta = await fetch("/api/convert", {{ method: "POST", body: datos }});
          if (!respuesta.ok) {{
            const texto = await respuesta.text();
            let mensaje = "No se ha podido generar el proyecto (HTTP " + respuesta.status + ").";
            try {{
              const cuerpo = JSON.parse(texto);
              if (cuerpo.error) mensaje = cuerpo.error;
            }} catch (e) {{
              const limpio = texto.replace(/<[^>]+>/g, " ").replace(/\\s+/g, " ").trim();
              if (limpio) mensaje += " " + limpio.slice(0, 500);
            }}
            throw new Error(mensaje);
          }}
          const blob = await respuesta.blob();
          const avisos = respuesta.headers.get("X-Avisos");
          const dispo = respuesta.headers.get("Content-Disposition") || "";
          const coincidencia = dispo.match(/filename="([^"]+)"/);
          const descarga = coincidencia ? coincidencia[1] : "qlik2pbi.zip";
          const url = URL.createObjectURL(blob);
          const enlace = document.createElement("a");
          enlace.href = url;
          enlace.download = descarga;
          enlace.click();
          URL.revokeObjectURL(url);
          estado.textContent = avisos && avisos !== "0"
            ? "ZIP descargado. El INFORME.md del paquete lista " + avisos + " avisos para revisar."
            : "ZIP descargado. Ábrelo en Power BI Desktop.";
        }} catch (error) {{
          estado.className = "aviso";
          estado.textContent = error.message;
        }} finally {{
          boton.disabled = false;
        }}
      }});
      function recortar(valor) {{
        if (Array.isArray(valor)) return valor.map(recortar);
        if (!valor || typeof valor !== "object") return valor;
        const copia = {{}};
        for (const clave of Object.keys(valor)) {{
          if (clave === "allFields" || clave === "qDataPages" || clave === "qMatrix" || clave === "qStackedDataPages" || clave === "qPivotDataPages" || clave === "qGrandTotalRow") continue;
          copia[clave] = recortar(valor[clave]);
        }}
        return copia;
      }}
    </script>
    """
    return _documento(
        "Sube el script de carga (.qvs) y la app de Qlik Sense (.qvf). El proyecto se genera en memoria.",
        cuerpo.replace("__JS_EXTRAER__", JS_EXTRAER),
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
