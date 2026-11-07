// The two small pages that check one link: a ResearchBox and a PsyArXiv preprint. The page
// says which it is in <body data-page="...">; the table below holds what each one sends.
"use strict";

(function () {
  const { runJob, resultButtons, statusBox, working } = window.mc;

  const PAGES = {
    researchbox: {
      // a box number, the address of a box, or a peer-review link (its passcode goes along)
      parse(text) {
        const t = text.trim();
        const m = /^(?:https?:\/\/)?(?:www\.)?researchbox\.org\/(\d{1,7})(&PEER_REVIEW_passcode=[A-Za-z0-9]+)?(?:[/?#].*)?$/i.exec(t) || /^#?(\d{1,7})$/.exec(t);
        if (!m) throw new Error("Enter a ResearchBox number, such as 1234, an address such as https://researchbox.org/1234, or a peer-review link.");
        return { id: m[1], fields: { kind: "repocheck", url: "https://researchbox.org/" + m[1] + (m[2] || "") } };
      },
      modules: () => null,
    },
    psyarxiv: {
      // the server checks the link again and keeps only the preprint's id
      parse(text) {
        const t = text.trim();
        if (!t) throw new Error("Enter the link or the id of a PsyArXiv preprint.");
        if (t.length > 300) throw new Error("This is too long to be a PsyArXiv link.");
        return { id: t, fields: { kind: "report", psyarxiv: t } };
      },
      // every check the server offers
      modules: async () => {
        const response = await fetch(window.mc.API + "/modules");
        if (!response.ok) throw new Error("The list of checks could not be loaded. Reload the page.");
        const info = await response.json();
        return info.allowed;
      },
    },
  };

  const page = PAGES[document.body.dataset.page];
  if (!page) return;
  const section = document.getElementById("tool-link");
  const input = section.querySelector(".link");
  const button = section.querySelector("button.go");
  const box = statusBox(section.querySelector(".status"));

  input.addEventListener("input", () => {
    button.disabled = !input.value.trim();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !button.disabled) button.click();
  });

  button.addEventListener("click", async () => {
    button.disabled = true;
    working(true);
    try {
      const target = page.parse(input.value);
      const fields = { ...target.fields, report_type: "checklist" };
      const modules = await page.modules();
      if (modules) fields.modules = modules.join(",");
      const crossref = section.querySelector(".crossref");
      if (crossref) fields.crossref = crossref.checked ? "true" : "false";
      fields.run_code = section.querySelector(".run-code").checked ? "true" : "false";
      const blob = await runJob(fields, null, null, box);
      box.results(resultButtons(blob, fields.kind, "report"));
    } catch (e) {
      box.error(e.message || String(e));
    } finally {
      working(false);
      button.disabled = !input.value.trim();
    }
  });

  window.mc.health().then((info) => {
    if (info && !info.run_code) {
      const tick = section.querySelector(".run-code");
      tick.checked = false;
      tick.disabled = true;
      section.querySelector(".run-code-note").textContent = "(this server does not run code)";
    }
  });
})();
