// Private research controls. Records and drafts remain within this unlocked page.
function researchControl(label, kind = "input") {
  const control = document.createElement(kind);
  const wrapper = document.createElement("label");
  wrapper.append(document.createTextNode(label), control);
  return { control, wrapper };
}
function researchSection(title) {
  const section = document.createElement("details");
  const summary = document.createElement("summary"); summary.textContent = title;
  section.append(summary); return section;
}
function researchButton(label, action) {
  const button = document.createElement("button"); button.type = "button"; button.textContent = label;
  button.addEventListener("click", action); return button;
}
function researchSelect(label, choices) {
  const field = researchControl(label, "select");
  choices.forEach(([value, text]) => { const option = document.createElement("option"); option.value = value; option.textContent = text; field.control.append(option); });
  return field;
}
async function renderPrivateResearch(site, root) {
  const epoch = state.authEpoch, generation = state.detailGeneration;
  const current = () => state.token && epoch === state.authEpoch && generation === state.detailGeneration && root.isConnected;
  const section = researchSection("Forskningsspørsmål og alternative identiteter");
  section.dataset.detailSection = "research-questions";
  const explanation = document.createElement("p"); explanation.textContent = "Spørsmål og hypoteser er private vurderinger. De endrer ikke kartpunkt, status eller adgang."; section.append(explanation);
  const list = document.createElement("div"); section.append(list);
  const form = document.createElement("form");
  const kind = researchSelect("Spørsmålstype", [["identity","Identitet"],["location","Plassering"],["access","Adgang"],["source","Kilde"],["other","Annet"]]);
  const wording = researchControl("Uavklart spørsmål", "textarea"); wording.control.required = true; wording.control.maxLength = 2000;
  const uncertainty = researchControl("Usikkerhet", "textarea"); uncertainty.control.maxLength = 2000;
  const sources = researchControl("Kildeadresser, én per linje", "textarea");
  const status = document.createElement("p"); status.setAttribute("role", "status");
  const submit = document.createElement("button"); submit.type = "submit"; submit.textContent = "Legg til spørsmål";
  form.append(kind.wrapper, wording.wrapper, uncertainty.wrapper, sources.wrapper, submit, status); section.append(form); root.append(section);
  async function refresh() {
    try {
      const response = await api(`/api/research/questions?external_key=${encodeURIComponent(site.external_key)}`);
      if (!current()) return;
      list.replaceChildren();
      for (const question of response.items) {
        const item = document.createElement("article");
        const heading = document.createElement("h4"); heading.textContent = question.wording || "Lagret spørsmål utilgjengelig";
        const stateLabel = document.createElement("p"); stateLabel.textContent = `${question.kind} · ${question.state} · revisjon ${question.revision}`;
        item.append(heading, stateLabel);
        if (question.data_status === "valid") {
          const note = document.createElement("p"); note.textContent = question.uncertainty || "Usikkerhet ikke nærmere beskrevet"; item.append(note);
          const choice = researchSelect("Vurdering", [["open","Åpent"],["deferred","Utsatt"],["resolved","Avklart"]]); choice.control.value = question.state;
          const reason = researchControl("Begrunnelse for vurderingen", "textarea"); reason.control.maxLength = 2000;
          item.append(choice.wrapper, reason.wrapper, researchButton("Lagre vurdering", async () => {
            if (!reason.control.value.trim()) { status.textContent = "En begrunnelse kreves."; return; }
            try { await api(`/api/research/questions/${question.id}`, { method:"PATCH", body:JSON.stringify({expected_revision:question.revision,state:choice.control.value,reason:reason.control.value.trim()}) }); if (current()) await refresh(); }
            catch (error) { if (current() && !isStaleRequest(error)) status.textContent = error.message; }
          }));
          if (["identity", "location"].includes(question.kind)) {
            const alternatives = document.createElement("div"); item.append(alternatives);
            api(`/api/research/questions/${question.id}`).then(detail => {
              if (!current()) return;
              for (const hypothesis of detail.hypotheses) {
                const label = document.createElement("p"); label.textContent = `Alternativ: ${hypothesis.label} (${hypothesis.state}). ${hypothesis.original_wording}. ${hypothesis.objections || ""}`; alternatives.append(label);
              }
            }).catch(error => { if (current() && !isStaleRequest(error)) status.textContent = error.message; });
            const label = researchControl("Navn på alternativ identitet");
            const original = researchControl("Opprinnelig kildeordlyd", "textarea");
            const objections = researchControl("Innvendinger", "textarea");
            item.append(label.wrapper, original.wrapper, objections.wrapper, researchButton("Behold nytt alternativ", async () => {
              if (!label.control.value.trim() || !original.control.value.trim()) { status.textContent = "Navn og opprinnelig ordlyd kreves."; return; }
              try { await api(`/api/research/questions/${question.id}/hypotheses`, {method:"POST",body:JSON.stringify({expected_question_revision:question.revision,label:label.control.value.trim(),original_wording:original.control.value.trim(),objections:objections.control.value.trim() || undefined})}); if (current()) await refresh(); }
              catch(error) { if (current() && !isStaleRequest(error)) status.textContent = error.message; }
            }));
          }
        }
        list.append(item);
      }
      if (!response.items.length) list.textContent = "Ingen private forskningsspørsmål er registrert for stedet.";
    } catch (error) { if (current() && !isStaleRequest(error)) status.textContent = error.message; }
  }
  form.addEventListener("submit", async event => {
    event.preventDefault(); submit.disabled = true;
    try {
      await api("/api/research/questions", {method:"POST",body:JSON.stringify({external_key:site.external_key,kind:kind.control.value,wording:wording.control.value.trim(),uncertainty:uncertainty.control.value.trim() || undefined,source_urls:sources.control.value.split(/\r?\n/).map(value=>value.trim()).filter(Boolean)})});
      if (current()) { form.reset(); status.textContent = "Spørsmål registrert."; await refresh(); }
    } catch (error) { if (current() && !isStaleRequest(error)) status.textContent = error.message; }
    finally { if (current()) submit.disabled = false; }
  });
  await refresh();
}
document.addEventListener("bunkerkartet:detail", event => { renderPrivateResearch(event.detail.site, event.detail.root); });
