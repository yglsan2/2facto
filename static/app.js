(function () {
  const root = document.documentElement;
  const aide = document.getElementById("aide");
  if (aide) {
    const off = root.classList.contains("aide-off");
    aide.setAttribute("aria-pressed", off ? "false" : "true");
    aide.addEventListener("click", function () {
      const hidden = root.classList.toggle("aide-off");
      localStorage.setItem("2facto-aide", hidden ? "0" : "1");
      aide.setAttribute("aria-pressed", hidden ? "false" : "true");
      closeTips();
    });
  }

  function closeTips() {
    document.querySelectorAll(".tip-pop.open").forEach(function (pop) {
      pop.classList.remove("open");
      const btn = pop.previousElementSibling;
      if (btn) btn.setAttribute("aria-expanded", "false");
    });
  }

  function place(pop, btn) {
    const box = btn.getBoundingClientRect();
    const width = 280;
    let left = Math.min(box.left, window.innerWidth - width - 12);
    left = Math.max(8, left);
    pop.style.left = left + "px";
    pop.style.top = box.bottom + 8 + "px";
    pop.classList.add("open");
    const rect = pop.getBoundingClientRect();
    if (rect.bottom > window.innerHeight - 8) {
      pop.style.top = Math.max(8, box.top - rect.height - 8) + "px";
    }
  }

  document.addEventListener("click", function (event) {
    const btn = event.target.closest(".tip-btn");
    if (!btn) {
      if (!event.target.closest(".tip-pop")) closeTips();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const pop = btn.nextElementSibling;
    const was = pop.classList.contains("open");
    closeTips();
    if (!was) {
      btn.setAttribute("aria-expanded", "true");
      place(pop, btn);
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeTips();
  });
  window.addEventListener("scroll", closeTips, true);

  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const node = document.querySelector(btn.getAttribute("data-copy"));
      if (!node) return;
      navigator.clipboard.writeText(node.value).then(function () {
        const old = btn.textContent;
        btn.textContent = "Copié";
        setTimeout(function () { btn.textContent = old; }, 1200);
      });
    });
  });

  document.addEventListener("submit", function (event) {
    const form = event.target;
    const submitter = event.submitter;
    const message = (submitter && submitter.dataset.confirm) || form.dataset.confirmForm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    if (form.id === "editor") stripEmpty(form);
  });

  const editor = document.getElementById("editor");
  if (!editor) return;
  const data = JSON.parse(document.getElementById("preview-data").textContent);
  const body = document.querySelector("#lines tbody");
  const tpl = document.getElementById("line-tpl");

  function iso(date) {
    const z = function (n) { return String(n).padStart(2, "0"); };
    return date.getFullYear() + "-" + z(date.getMonth() + 1) + "-" + z(date.getDate());
  }
  function addDays(value, days) {
    const parts = value.split("-").map(Number);
    const date = new Date(parts[0], parts[1] - 1, parts[2]);
    date.setDate(date.getDate() + Number(days || 0));
    return iso(date);
  }
  function money(text) {
    const raw = String(text || "").replace(/\s/g, "").replace(",", ".");
    if (!raw) return 0;
    const n = Number(raw);
    return Number.isFinite(n) ? Math.round(n * 100) : 0;
  }
  function percent(text) {
    const raw = String(text || "0").replace(/\s/g, "").replace(",", ".");
    const n = Number(raw);
    return Number.isFinite(n) ? Math.round(n * 100) : 0;
  }
  function lineCents(qtyBp, price, remiseBp) {
    return Math.trunc((qtyBp * price * (10000 - remiseBp) + 500000) / 1000000);
  }
  function allowance(ht, remiseBp) {
    return Math.trunc((ht * remiseBp + 5000) / 10000);
  }
  function vat(base, tauxBp) {
    return Math.trunc((base * tauxBp + 5000) / 10000);
  }
  function eur(cents) {
    const sign = cents < 0 ? "-" : "";
    cents = Math.abs(cents);
    const whole = Math.trunc(cents / 100);
    const frac = String(cents % 100).padStart(2, "0");
    return sign + whole.toLocaleString("fr-FR") + "," + frac + " €";
  }
  function rows() {
    return Array.from(body.querySelectorAll("tr"));
  }
  function readRow(tr) {
    return {
      libelle: tr.querySelector('[name="libelle"]').value.trim(),
      qty: percent(tr.querySelector('[name="quantite"]').value || "1"),
      unite: tr.querySelector('[name="unite"]').value,
      prix: money(tr.querySelector('[name="prix"]').value),
      remise: percent(tr.querySelector('[name="remise"]').value),
    };
  }
  function stripEmpty(form) {
    rows().forEach(function (tr) {
      const line = readRow(tr);
      if (!line.libelle && !line.prix && rows().length > 1) tr.remove();
    });
  }
  function seedDates() {
    const issue = editor.querySelector("#issue");
    const service = editor.querySelector("#service");
    const due = editor.querySelector("#due");
    if (issue && !issue.value) issue.value = iso(new Date());
    if (service && !service.value && issue && issue.value) service.value = issue.value;
    if (due && due.dataset.auto === "1" && issue && issue.value) due.value = addDays(issue.value, data.delai);
  }
  function paint() {
    const issue = editor.querySelector("#issue");
    const due = editor.querySelector("#due");
    if (due && due.dataset.auto === "1" && issue && issue.value) due.value = addDays(issue.value, data.delai);
    const year = (issue && issue.value || "").slice(0, 4);
    const number = data.numbers[year] || Object.values(data.numbers)[0] || "";
    const label = document.getElementById("next-num");
    if (label && number) {
      const tip = label.querySelector(".tip");
      label.childNodes[0].textContent = "Sera numéroté " + number + " ";
      if (tip && !label.contains(tip)) label.appendChild(tip);
    }
    let ht = 0;
    const lines = [];
    rows().forEach(function (tr) {
      const line = readRow(tr);
      if (!line.libelle && !line.prix) return;
      const amount = lineCents(line.qty || 100, line.prix, line.remise);
      ht += amount;
      lines.push({ line: line, amount: amount });
    });
    const remiseBp = percent((editor.querySelector("#remise") || {}).value);
    const cut = allowance(ht, remiseBp);
    const base = ht - cut;
    const taux = data.tva ? (data.taux_bp || 2000) : 0;
    const tax = data.tva ? vat(base, taux) : 0;
    const ttc = base + tax;
    const net = document.getElementById("net");
    if (net) net.textContent = eur(ttc);
    const clientId = (editor.querySelector('[name="client_id"]') || {}).value;
    const client = data.clients[clientId] || null;
    const nature = (editor.querySelector("#nature") || {}).value || "prestation";
    const objet = (editor.querySelector('[name="objet"]') || {}).value || "";
    const bits = [];
    bits.push("<h3>" + escape(data.seller) + "</h3>");
    bits.push("<p class='muted'>Entrepreneur individuel<br>SIREN " + escape(data.siren) + "</p>");
    if (client) {
      bits.push("<p><strong>" + escape(client.nom) + "</strong><br>");
      if (!client.masquer && client.adresse) bits.push(escape(client.adresse) + "<br>");
      if (!client.masquer) bits.push(escape((client.cp + " " + client.ville).trim()));
      if (client.siren) bits.push("<br>SIREN " + escape(client.siren));
      bits.push("</p>");
    } else {
      bits.push("<p class='muted'>Choisissez un client</p>");
    }
    if (objet) bits.push("<p><strong>" + escape(objet) + "</strong></p>");
    bits.push("<p class='right muted'>" + escape(number) + "</p>");
    bits.push("<table>");
    lines.forEach(function (item) {
      const unit = data.unites[item.line.unite] || "";
      bits.push("<tr><td>" + escape(item.line.libelle || "Prestation") + "<br><span class='muted'>" + (item.line.qty / 100) + " " + escape(unit) + "</span></td><td class='right'>" + eur(item.amount) + "</td></tr>");
    });
    bits.push("</table>");
    if (!data.tva) bits.push("<p class='muted'>" + escape(data.mention) + "</p>");
    bits.push("<p class='right'><strong>" + eur(ttc) + "</strong></p>");
    bits.push("<p class='muted'>" + escape(data.natures[nature] || "") + "</p>");
    if (client && client.type === "professionnel") bits.push("<p class='muted'>Indemnité forfaitaire de 40 € en cas de retard.</p>");
    if (client && client.type === "particulier" && data.mediateur) bits.push("<p class='muted'>Médiateur : " + escape(data.mediateur) + "</p>");
    if (data.iban) bits.push("<p class='muted'>IBAN " + escape(data.iban) + "</p>");
    document.getElementById("sheet").innerHTML = bits.join("");
  }
  function escape(value) {
    return String(value || "").replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
    });
  }
  function bind(tr) {
    tr.querySelectorAll("input, select").forEach(function (field) {
      field.addEventListener("input", paint);
      field.addEventListener("change", paint);
    });
    const drop = tr.querySelector(".drop");
    if (drop) drop.addEventListener("click", function () {
      if (rows().length === 1) {
        tr.querySelectorAll("input").forEach(function (field) {
          if (field.name === "quantite") field.value = "1";
          else if (field.name === "remise") field.value = "0";
          else field.value = "";
        });
      } else {
        tr.remove();
      }
      paint();
    });
  }
  rows().forEach(bind);
  document.getElementById("add-line").addEventListener("click", function () {
    const tr = tpl.content.firstElementChild.cloneNode(true);
    body.appendChild(tr);
    bind(tr);
    paint();
  });
  document.querySelectorAll(".chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      let tr = rows().find(function (row) { return !readRow(row).libelle && !readRow(row).prix; });
      if (!tr) {
        tr = tpl.content.firstElementChild.cloneNode(true);
        body.appendChild(tr);
        bind(tr);
      }
      tr.querySelector('[name="libelle"]').value = chip.dataset.libelle || "";
      tr.querySelector('[name="unite"]').value = chip.dataset.unite || "forfait";
      if (chip.dataset.prix) tr.querySelector('[name="prix"]').value = chip.dataset.prix;
      paint();
    });
  });
  editor.querySelectorAll("input, select, textarea").forEach(function (field) {
    field.addEventListener("input", paint);
    field.addEventListener("change", function () {
      if (field.id === "due") field.dataset.auto = "0";
      paint();
    });
  });
  seedDates();
  paint();
})();
