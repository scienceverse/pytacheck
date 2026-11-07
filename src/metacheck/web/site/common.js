// Shared by the pages of the Metacheck demo site: small DOM helpers, the box that shows what a
// job is doing, and sending a job to the web interface (POST /api/jobs), following it
// (GET /api/jobs/<id>, every 2 seconds) and fetching its result. Everything stays on this
// server: the pages make no request anywhere else.
"use strict";

window.mc = (function () {
  const API = "/api";
  const MB = 1024 * 1024;
  const POLL_MS = 2000;

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(props || {})) {
      if (name === "class") node.className = value;
      else node.setAttribute(name, value);
    }
    node.append(...children);
    return node;
  }

  function sizeText(bytes) {
    if (bytes >= MB) return (bytes / MB).toFixed(bytes >= 10 * MB ? 0 : 1) + " MB";
    return Math.max(1, Math.round(bytes / 1024)) + " kB";
  }

  function stem(name) {
    return (name || "").replace(/^.*[\\/]/, "").replace(/\.[^.]*$/, "") || "paper";
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // The error a server answered with: its own text when it sent one.
  async function errorText(response) {
    try {
      const body = await response.json();
      if (body && body.error) return body.error;
    } catch (e) {
      /* not JSON */
    }
    if ([502, 503, 504].includes(response.status)) {
      return "The server is not answering at the moment (HTTP " + response.status + "). Try again in a few minutes.";
    }
    return "The server answered with an error (HTTP " + response.status + ").";
  }

  // The box under a button: a bar and a line of text, then a result or an error.
  function statusBox(box) {
    const bar = el("div", { class: "bar unknown" }, el("span"));
    const line = el("p", { class: "step", "aria-live": "polite" });
    return {
      show(fraction, text) {
        if (!box.contains(bar)) box.replaceChildren(bar, line);
        box.hidden = false;
        if (fraction === null || fraction === undefined) {
          bar.classList.add("unknown");
          bar.firstChild.style.width = "";
        } else {
          bar.classList.remove("unknown");
          bar.firstChild.style.width = Math.round(Math.min(1, Math.max(0, fraction)) * 100) + "%";
        }
        line.textContent = text;
      },
      error(message) {
        box.hidden = false;
        box.replaceChildren(el("div", { class: "error", role: "alert" }, ...[].concat(message)));
      },
      results(nodes) {
        box.hidden = false;
        box.replaceChildren(el("div", { class: "results" }, ...nodes));
      },
      hide() {
        box.hidden = true;
        box.replaceChildren();
      },
    };
  }

  // POST /api/jobs with the upload's progress (fetch cannot tell it, XMLHttpRequest can).
  function post(form, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", API + "/jobs");
      xhr.responseType = "blob";
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => resolve(new Response(xhr.response, { status: xhr.status }));
      xhr.onerror = () => reject(new Error("The upload failed. Check your connection and try again."));
      xhr.send(form);
    });
  }

  // Send a job and wait for it. `fields` are the form fields, `file` an optional Blob.
  // Returns the result as a Blob. `box` is a statusBox.
  async function runJob(fields, file, filename, box) {
    const form = new FormData();
    for (const [name, value] of Object.entries(fields)) form.append(name, value);
    if (file) form.append("file", file, filename);

    box.show(0, file ? "Uploading " + sizeText(file.size) + "…" : "Sending the request…");
    const sent = await post(form, (f) => box.show(f, "Uploading: " + Math.round(f * 100) + "%"));
    if (sent.status !== 202) throw new Error(await errorText(sent));
    const job = await sent.json();

    let failures = 0;
    for (;;) {
      let status;
      try {
        const polled = await fetch(job.status_url, { cache: "no-store" });
        if (!polled.ok) throw new Error(await errorText(polled));
        status = await polled.json();
        failures = 0;
      } catch (e) {
        if (++failures >= 5) throw e;
        await sleep(3000);
        continue;
      }
      if (status.status === "queued") {
        const place = status.position ? " (place " + status.position + ")" : "";
        box.show(null, "Waiting in the queue" + place + "…");
      } else if (status.status === "running") {
        const step = status.step ? status.step.charAt(0).toUpperCase() + status.step.slice(1) : "Working";
        box.show(typeof status.progress === "number" ? status.progress : null, step + "…");
      } else if (status.status === "failed") {
        throw new Error(status.error || "The job failed.");
      } else if (status.status === "done") {
        break;
      }
      await sleep(POLL_MS);
    }

    box.show(1, "Fetching the result…");
    const result = await fetch(job.result_url, { cache: "no-store" });
    if (!result.ok) throw new Error(await errorText(result));
    return result.blob();
  }

  // The buttons that open and save a result held in this page (the server forgets it after an hour).
  function resultButtons(blob, kind, name) {
    const json = kind === "convert";
    const typed = new Blob([blob], { type: json ? "application/json" : "text/html" });
    const url = URL.createObjectURL(typed);
    if (json) {
      return [
        el("a", { class: "button", href: url, download: name + ".json" }, "Download the JSON"),
        el("a", { class: "button plain", href: url, target: "_blank", rel: "noopener" }, "Open the JSON"),
      ];
    }
    return [
      el("a", { class: "button", href: url, target: "_blank", rel: "noopener" }, "Open the report"),
      el("a", { class: "button plain", href: url, download: name + "_metacheck_report.html" }, "Download the report"),
      el("p", { class: "note" }, "The server keeps the report for one hour. Download it to keep it."),
    ];
  }

  // Fill the footer with the server's version, and hand the health answer on.
  async function health() {
    try {
      const response = await fetch(API + "/health", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const info = await response.json();
      const where = document.getElementById("version");
      if (where && info.metacheck_version) where.textContent = "Version " + info.metacheck_version + ".";
      return info;
    } catch (e) {
      return null;
    }
  }

  // Keep a visitor from closing the page while a job is being sent or followed.
  let busy = 0;
  window.addEventListener("beforeunload", (e) => {
    if (busy > 0) {
      e.preventDefault();
      e.returnValue = "";
    }
  });
  function working(on) {
    busy += on ? 1 : -1;
  }

  return { API, MB, el, sizeText, stem, sleep, errorText, statusBox, runJob, resultButtons, health, working };
})();
