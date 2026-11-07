// The main page: check a paper, check a repository. A folder is packed into a .zip here in the
// browser (JSZip, served from this server) before it is sent. The limits below are those of
// the web interface (docs/SITE.md); the server checks them again.
"use strict";

(function () {
  const { el, sizeText, stem, runJob, resultButtons, statusBox, working, MB } = window.mc;
  const LIMITS = { paper: 50 * MB, zip: 500 * MB, unpacked: 1000 * MB, files: 20000 };
  // version control folders and the files a file manager leaves behind are not packed
  const SKIP = /(^|\/)(\.git|\.svn|\.hg)(\/|$)|(^|\/)(\.DS_Store|Thumbs\.db|desktop\.ini)$/i;
  const SHRINK = " Leave out large files that need no checking (recordings, images, videos), or paste the link of the online repository instead.";
  let health = null;

  // ---- running a job from a tool ------------------------------------------------------

  async function start(section, kind, fields, prepare) {
    const button = section.querySelector("button.go");
    const box = statusBox(section.querySelector(".status"));
    button.disabled = true;
    working(true);
    try {
      const { file, filename, name } = await prepare(box);
      const blob = await runJob({ kind, ...fields }, file, filename, box);
      box.results(resultButtons(blob, kind, name));
    } catch (e) {
      box.error(e.nodes || e.message || String(e));
    } finally {
      working(false);
      button.disabled = false;
    }
  }

  // ---- the paper tool -----------------------------------------------------------------

  function setupPaper(section) {
    const drop = section.querySelector(".drop");
    const input = drop.querySelector("input");
    const text = drop.querySelector(".drop-text");
    const button = section.querySelector("button.go");
    const box = statusBox(section.querySelector(".status"));
    let chosen = null;

    function choose(file) {
      chosen = null;
      button.disabled = true;
      drop.classList.remove("chosen");
      if (!file) return;
      if (!/\.(pdf|json|xml)$/i.test(file.name)) {
        box.error("Choose a PDF, a bibr JSON file or a GROBID XML file.");
        return;
      }
      if (file.size > LIMITS.paper) {
        box.error("This file is " + sizeText(file.size) + "; the limit is 50 MB.");
        return;
      }
      box.hide();
      chosen = file;
      text.textContent = file.name + " (" + sizeText(file.size) + ")";
      drop.classList.add("chosen");
      button.disabled = false;
    }

    input.addEventListener("change", () => choose(input.files[0]));
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      choose(e.dataTransfer.files[0]);
    });

    button.addEventListener("click", () => {
      if (!chosen) return;
      const modules = [...section.querySelectorAll(".check-list input:checked")].map((i) => i.value);
      if (!modules.length) {
        box.error('Choose at least one check under "Checks".');
        return;
      }
      const fields = {
        modules: modules.join(","),
        report_type: "checklist",
        crossref: section.querySelector(".crossref").checked ? "true" : "false",
        run_code: section.querySelector(".run-code").checked ? "true" : "false",
      };
      const file = chosen;
      start(section, "report", fields, async () => ({ file, filename: file.name, name: stem(file.name) }));
    });
  }

  // The checks, from GET /api/modules: the default set ticked.
  async function listChecks(section) {
    const list = section.querySelector(".check-list");
    const count = section.querySelector(".count");
    try {
      const response = await fetch(window.mc.API + "/modules");
      if (!response.ok) throw new Error("HTTP " + response.status);
      const modules = await response.json();
      const rows = modules.info.length
        ? modules.info
        : modules.allowed.map((name) => ({ name, title: name, description: "", default: modules.default.includes(name) }));
      const update = () => {
        const all = list.querySelectorAll("input").length;
        const on = list.querySelectorAll("input:checked").length;
        count.textContent = "(" + on + " of " + all + ")";
      };
      const group = (heading, items) =>
        items.length
          ? [
              el("h3", {}, heading),
              ...items.map((row) => {
                const tick = el("input", { type: "checkbox", value: row.name });
                tick.checked = row.default;
                tick.addEventListener("change", update);
                return el("label", { title: row.description }, tick, el("span", {}, row.title || row.name));
              }),
            ]
          : [];
      list.replaceChildren(
        ...group("Default checks", rows.filter((r) => r.default)),
        ...group("Other checks (some contact other services)", rows.filter((r) => !r.default))
      );
      update();
    } catch (e) {
      list.replaceChildren(el("p", { class: "note" }, "The list of checks could not be loaded. Reload the page."));
    }
  }

  // ---- the repository tool ------------------------------------------------------------

  // Every file under a dropped folder. readEntries gives at most about 100 entries a call, so it
  // is called until it gives none. A file or folder the browser cannot open is kept with its
  // error, for unreadable().
  async function walk(entry, out) {
    const path = entry.fullPath.replace(/^\/+/, "");
    if (entry.isFile) {
      try {
        out.push({ file: await new Promise((ok, no) => entry.file(ok, no)), path });
      } catch (e) {
        out.push({ file: null, path, failed: true });
      }
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      for (;;) {
        let batch;
        try {
          batch = await new Promise((ok, no) => reader.readEntries(ok, no));
        } catch (e) {
          out.push({ file: null, path: path + "/", failed: true });
          return;
        }
        if (!batch.length) return;
        for (const child of batch) await walk(child, out);
      }
    }
  }

  // The browser lists some files it cannot read (a path over 260 characters on Windows, a file
  // that is only online in OneDrive, a file moved since). Packing would stop at the first one
  // with a message that names none, so every file is tried first. Returns the paths that failed.
  async function unreadable(files, onProgress) {
    const bad = [];
    let next = 0;
    let done = 0;
    async function worker() {
      while (next < files.length) {
        const item = files[next++];
        if (item.failed) {
          bad.push(item.path);
        } else {
          try {
            await (item.file.size === 0 ? item.file : item.file.slice(0, 1)).arrayBuffer();
          } catch (e) {
            bad.push(item.path);
          }
        }
        if (++done % 100 === 0) onProgress(done / files.length);
      }
    }
    await Promise.all(Array.from({ length: 8 }, worker));
    return bad.sort();
  }

  function unreadableMessage(bad, total) {
    const shown = bad.slice(0, 5).map((p) => el("li", { title: p }, p.replace(/^.*\//, "") || p));
    if (bad.length > 5) shown.push(el("li", {}, "and " + (bad.length - 5) + " more"));
    return [
      el("p", {}, "The browser could not read " + bad.length + " of the " + total + " files in the folder, so it cannot be checked as it is:"),
      el("ul", {}, ...shown),
      el("p", {}, "Common causes: a path longer than Windows allows (move the folder closer to the top of the drive), a file that OneDrive keeps online only (keep it on this device), or a file that was moved or opened elsewhere."),
      el("p", {}, "Or pack the folder into a .zip yourself and choose the .zip."),
    ];
  }

  function setupRepository(section) {
    const drop = section.querySelector(".drop");
    const text = drop.querySelector(".drop-text");
    const button = section.querySelector("button.go");
    const link = section.querySelector(".repo-url");
    const box = statusBox(section.querySelector(".status"));
    let chosen = null; // { zip }, { files, name } or { url }

    function reset() {
      chosen = null;
      button.disabled = true;
      drop.classList.remove("chosen");
      text.textContent = "Drop a folder or a .zip file here";
    }

    link.addEventListener("input", () => {
      const url = link.value.trim();
      if (url) {
        reset();
        box.hide();
        chosen = { url };
        button.disabled = false;
      } else if (chosen && chosen.url) {
        reset();
      }
    });

    function chooseZip(file) {
      reset();
      link.value = "";
      if (!/\.zip$/i.test(file.name)) {
        box.error("Choose a folder or a .zip file.");
        return;
      }
      if (file.size > LIMITS.zip) {
        box.error("This .zip file is " + sizeText(file.size) + "; the limit is 500 MB." + SHRINK);
        return;
      }
      box.hide();
      chosen = { zip: file };
      text.textContent = file.name + " (" + sizeText(file.size) + ")";
      drop.classList.add("chosen");
      button.disabled = false;
    }

    async function chooseFiles(all) {
      reset();
      link.value = "";
      const files = all.filter((f) => !SKIP.test(f.path));
      if (!files.length) {
        box.error("The folder has no files to check.");
        return;
      }
      if (files.length > LIMITS.files) {
        box.error("The folder has " + files.length + " files; the limit is 20,000. Leave out folders that need no checking, such as renv/ or node_modules/.");
        return;
      }
      const total = files.reduce((sum, f) => sum + (f.file ? f.file.size : 0), 0);
      if (total > LIMITS.unpacked) {
        box.error("The folder holds " + sizeText(total) + "; the limit is 1000 MB." + SHRINK);
        return;
      }
      text.textContent = "Reading the folder…";
      box.show(0, "Checking that every file can be read…");
      const bad = await unreadable(files, (f) => box.show(f, "Checking that every file can be read: " + Math.round(f * 100) + "%"));
      if (bad.length) {
        reset();
        box.error(unreadableMessage(bad, files.length));
        return;
      }
      box.hide();
      // a folder that holds one folder is named after that one
      const tops = new Set(files.map((f) => f.path.split("/")[0]));
      const name = tops.size === 1 && files.every((f) => f.path.includes("/")) ? [...tops][0] : "folder";
      chosen = { files, name };
      const left = all.length - files.length;
      text.textContent = name + ": " + files.length + " files, " + sizeText(total) + (left ? " (" + left + " version control files left out)" : "");
      drop.classList.add("chosen");
      button.disabled = false;
    }

    section.querySelector(".pick-zip").addEventListener("change", (e) => {
      if (e.target.files[0]) chooseZip(e.target.files[0]);
    });
    section.querySelector(".pick-folder").addEventListener("change", (e) => {
      const files = [...e.target.files].map((file) => ({ file, path: file.webkitRelativePath || file.name }));
      chooseFiles(files).catch((err) => {
        reset();
        box.error("The folder could not be read: " + (err.message || err));
      });
    });

    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", async (e) => {
      e.preventDefault();
      drop.classList.remove("over");
      // the entries must be taken before the first await
      const entries = [...e.dataTransfer.items].map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null)).filter(Boolean);
      const loose = [...e.dataTransfer.files];
      if (!entries.length) {
        if (loose.length === 1) chooseZip(loose[0]);
        return;
      }
      if (entries.length === 1 && entries[0].isFile) {
        chooseZip(loose[0]);
        return;
      }
      text.textContent = "Reading the folder…";
      try {
        const out = [];
        for (const entry of entries) await walk(entry, out);
        await chooseFiles(out);
      } catch (err) {
        reset();
        box.error("The folder could not be read: " + (err.message || err));
      }
    });

    button.addEventListener("click", () => {
      if (!chosen) return;
      const pick = chosen;
      const fields = { run_code: section.querySelector(".run-code").checked ? "true" : "false", report_type: "checklist" };
      if (pick.url) fields.url = pick.url;
      start(section, "repocheck", fields, async (progress) => {
        if (pick.url) {
          const name = pick.url.replace(/^https?:\/\//i, "").replace(/[/?#]+$/, "").split("/").filter(Boolean).pop() || "repository";
          return { file: null, filename: null, name };
        }
        if (pick.zip) return { file: pick.zip, filename: pick.zip.name, name: stem(pick.zip.name) };
        return pack(pick, progress);
      });
    });
  }

  // The folder as a .zip, made here.
  async function pack(pick, progress) {
    const zip = new window.JSZip();
    for (const item of pick.files) {
      zip.file(item.path, item.file, { date: new Date(item.file.lastModified || Date.now()) });
    }
    progress.show(0, "Packing the folder…");
    let blob;
    try {
      blob = await zip.generateAsync(
        { type: "blob", compression: "DEFLATE", compressionOptions: { level: 1 } },
        (meta) => progress.show(meta.percent / 100, "Packing the folder: " + Math.round(meta.percent) + "%")
      );
    } catch (err) {
      // a file changed since it was chosen, or the browser ran out of memory
      const bad = await unreadable(pick.files, () => {});
      if (bad.length) {
        const nodes = unreadableMessage(bad, pick.files.length);
        const e = new Error(nodes.map((n) => n.textContent).join(" "));
        e.nodes = nodes;
        throw e;
      }
      throw new Error("The browser could not pack the folder (" + (err.message || err) + "). Choose the folder again, or pack it into a .zip yourself and choose the .zip.");
    }
    if (blob.size > LIMITS.zip) {
      throw new Error("The packed folder is " + sizeText(blob.size) + "; the limit is 500 MB." + SHRINK);
    }
    return { file: blob, filename: pick.name + ".zip", name: pick.name };
  }

  // ---- the page ------------------------------------------------------------------------

  const paper = document.getElementById("tool-paper");
  const repository = document.getElementById("tool-repository");
  setupPaper(paper);
  setupRepository(repository);
  listChecks(paper);
  document.getElementById("curl-example").textContent =
    "curl -F file=@paper.pdf " + location.origin + "/api/report -o report.html";

  window.mc.health().then((info) => {
    health = info;
    if (!info) return;
    if (!info.run_code) {
      // this server does not run code: say so, and do not offer the box
      for (const section of [paper, repository]) {
        const tick = section.querySelector(".run-code");
        tick.checked = false;
        tick.disabled = true;
        section.querySelector(".run-code-note").textContent = "(this server does not run code)";
      }
    }
    const note = document.getElementById("converter-note");
    if (note) {
      note.textContent = info.bibr
        ? "A PDF is converted by the bibr service this server is set up with; if that does not answer, by a GROBID server."
        : info.grobid === "local"
          ? "A PDF is converted by a GROBID server on this computer."
          : "A PDF is converted by a GROBID server, which is another computer: the PDF is sent there. Send bibr JSON or GROBID XML instead to keep a paper on this server.";
    }
  });
})();
