// Página de chat: envía los mensajes a POST /chat y muestra las respuestas.
// La sesión vive en memoria: al recargar la página empieza una conversación nueva.
(function () {
  "use strict";

  const mensajes = document.getElementById("mensajes");
  const formulario = document.getElementById("formulario");
  const entrada = document.getElementById("entrada");
  const botonEnviar = document.getElementById("enviar");
  const botonNueva = document.getElementById("nueva");
  const avisoUrgencia = document.getElementById("aviso-urgencia");

  let sesionId = null;
  let nombreClinica = "la clínica";
  let enviando = false;

  function hora() {
    return new Date().toLocaleTimeString("es-PE", { hour: "2-digit", minute: "2-digit" });
  }

  function agregarMensaje(texto, autor, extraClase) {
    const burbuja = document.createElement("div");
    burbuja.className = "mensaje mensaje--" + autor + (extraClase ? " " + extraClase : "");
    const cuerpo = document.createElement("p");
    cuerpo.textContent = texto; // textContent: nunca se interpreta como HTML
    const sello = document.createElement("span");
    sello.className = "mensaje__hora";
    sello.textContent = hora();
    burbuja.append(cuerpo, sello);
    mensajes.append(burbuja);
    mensajes.scrollTop = mensajes.scrollHeight;
    return burbuja;
  }

  function mostrarEscribiendo() {
    const burbuja = document.createElement("div");
    burbuja.className = "mensaje mensaje--bot mensaje--escribiendo";
    burbuja.setAttribute("aria-label", "El asistente está escribiendo");
    burbuja.innerHTML = "<span></span><span></span><span></span>";
    mensajes.append(burbuja);
    mensajes.scrollTop = mensajes.scrollHeight;
    return burbuja;
  }

  function saludar() {
    agregarMensaje(
      "¡Hola! Soy el asistente virtual de " + nombreClinica +
        ". Puedo responder tus preguntas y ayudarte con tus citas. Para empezar, ¿me compartes tu número de celular?",
      "bot"
    );
  }

  function nuevaConversacion() {
    sesionId = null;
    avisoUrgencia.hidden = true;
    mensajes.replaceChildren();
    saludar();
    entrada.focus();
  }

  function ajustarAltura() {
    entrada.style.height = "auto";
    entrada.style.height = Math.min(entrada.scrollHeight, 140) + "px";
  }

  async function enviar(texto) {
    enviando = true;
    botonEnviar.disabled = true;
    agregarMensaje(texto, "cliente");
    const escribiendo = mostrarEscribiendo();

    try {
      const respuesta = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sesion_id: sesionId, mensaje: texto }),
      });
      const datos = await respuesta.json().catch(() => ({}));
      escribiendo.remove();

      if (!respuesta.ok) {
        const detalle =
          typeof datos.detail === "string" ? datos.detail : "No pude responder en este momento. Intenta de nuevo.";
        agregarMensaje(detalle, "bot", "mensaje--error");
        return;
      }
      sesionId = datos.sesion_id;
      agregarMensaje(datos.respuesta, "bot");
      if (datos.urgencia) avisoUrgencia.hidden = false;
    } catch (error) {
      escribiendo.remove();
      agregarMensaje("No hay conexión con el servidor. Revisa tu conexión e intenta de nuevo.", "bot", "mensaje--error");
    } finally {
      enviando = false;
      botonEnviar.disabled = false;
      entrada.focus();
    }
  }

  formulario.addEventListener("submit", function (evento) {
    evento.preventDefault();
    const texto = entrada.value.trim();
    if (!texto || enviando) return;
    entrada.value = "";
    ajustarAltura();
    enviar(texto);
  });

  // Enter envía; Shift+Enter hace un salto de línea.
  entrada.addEventListener("keydown", function (evento) {
    if (evento.key === "Enter" && !evento.shiftKey) {
      evento.preventDefault();
      formulario.requestSubmit();
    }
  });
  entrada.addEventListener("input", ajustarAltura);
  botonNueva.addEventListener("click", nuevaConversacion);

  fetch("/clinica")
    .then((r) => (r.ok ? r.json() : null))
    .then((datos) => {
      if (datos) {
        nombreClinica = datos.nombre;
        document.getElementById("nombre-clinica").textContent = datos.nombre;
        document.title = "Chat · " + datos.nombre;
        for (const id of ["tel-urgencias", "tel-urgencias-aviso"]) {
          const tel = document.getElementById(id);
          tel.textContent = datos.telefono_urgencias;
          tel.href = "tel:" + datos.telefono_urgencias.replace(/[^0-9+]/g, "");
        }
      }
    })
    .catch(() => {})
    .finally(nuevaConversacion);
})();
