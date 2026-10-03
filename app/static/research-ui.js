// Private research controls. Records and drafts remain within this unlocked page.
function researchControl(label, kind = "input") {
  const control = document.createElement(kind);
  control.setAttribute("aria-label", label);
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
  if (!hasSessionRole("owner")) return;
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
                const record = document.createElement("article"); alternatives.append(record);
                researchText(record, `Alternativ: ${hypothesis.label} (${hypothesis.state}). ${hypothesis.original_wording}. ${hypothesis.objections || ""}`);
                if (hypothesis.candidate_point) researchText(record, `Hypotesepunkt: ${hypothesis.candidate_point.lat}, ${hypothesis.candidate_point.lon}. Dette er ikke stedets kartpunkt eller en adkomst.`);
                if (hypothesis.decision_reason) researchText(record, `Tidligere begrunnelse: ${hypothesis.decision_reason}`);
                const decision = researchSelect("Vurder alternativ", [["retained","Behold"],["rejected","Forkast"],["deferred","Utsett"]]); decision.control.value = hypothesis.state;
                const reason = researchControl("Begrunnelse for alternativet", "textarea"); reason.control.maxLength = 2000;
                record.append(decision.wrapper, reason.wrapper, researchButton("Registrer alternativvurdering", async () => {
                  if (!reason.control.value.trim()) { status.textContent = "En begrunnelse kreves."; return; }
                  try { await api(`/api/research/hypotheses/${hypothesis.id}`, {method:"PATCH",body:JSON.stringify({expected_revision:hypothesis.revision,expected_question_revision:detail.revision,state:decision.control.value,reason:reason.control.value.trim()})}); if(current()) await refresh(); }
                  catch(error) { if(current() && !isStaleRequest(error)) status.textContent = error.message; }
                }));
              }
            }).catch(error => { if (current() && !isStaleRequest(error)) status.textContent = error.message; });
            const label = researchControl("Navn på alternativ identitet");
            const original = researchControl("Opprinnelig kildeordlyd", "textarea");
            const objections = researchControl("Innvendinger", "textarea");
            const pointLat = researchControl("Hypotesens breddegrad (valgfri)"), pointLon = researchControl("Hypotesens lengdegrad (valgfri)");
            item.append(label.wrapper, original.wrapper, objections.wrapper, pointLat.wrapper, pointLon.wrapper, researchButton("Behold nytt alternativ", async () => {
              if (!label.control.value.trim() || !original.control.value.trim()) { status.textContent = "Navn og opprinnelig ordlyd kreves."; return; }
              const lat = pointLat.control.value.trim(), lon = pointLon.control.value.trim();
              if (Boolean(lat) !== Boolean(lon) || (lat && (!Number.isFinite(Number(lat)) || !Number.isFinite(Number(lon))))) { status.textContent = "Oppgi begge koordinater som endelige tall."; return; }
              try { await api(`/api/research/questions/${question.id}/hypotheses`, {method:"POST",body:JSON.stringify({expected_question_revision:question.revision,label:label.control.value.trim(),original_wording:original.control.value.trim(),objections:objections.control.value.trim() || undefined,candidate_point:lat ? {lat:Number(lat),lon:Number(lon)} : undefined})}); if (current()) await refresh(); }
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

function researchText(parent, text, tag = "p") {
  const element = document.createElement(tag); element.textContent = text; parent.append(element); return element;
}
function researchJSON(parent, value) { return researchText(parent, JSON.stringify(value, null, 2), "pre"); }
async function renderCuratorTools(site, root) {
  if (!hasSessionRole("owner")) return;
  const epoch = state.authEpoch, generation = state.detailGeneration;
  const current = () => Boolean(state.token && epoch === state.authEpoch && generation === state.detailGeneration && root.isConnected);
  const message = document.createElement("p"); message.setAttribute("role", "status"); root.append(message);
  const attempt = async action => { try { await action(); } catch(error) { if(current() && !isStaleRequest(error)) message.textContent = error.message; } };
  const refresh = () => { if(current()) openDetail(site.id); };

  const sourceSection = researchSection("Kilder, daterte lesninger og arkivhenvisninger"); sourceSection.dataset.detailSection = "source-navigation";
  researchText(sourceSection, "Kilderegister, kuraterte kilde-ID-er og daterte lesninger beholdes hver for seg. Manglende dato eller sidetall er ukjent.");
  const urls = new Set([...(site.sources || []).map(item=>item.url), ...(site.content_document?.sources || []).map(item=>item.url)]);
  for(const url of urls) sourceSection.append(researchButton(url, ()=>attempt(async()=> {
    const source = await api(`/api/research/source?url=${encodeURIComponent(url)}`); if(!current())return;
    const result = document.createElement("article"); sourceSection.append(result);
    researchJSON(result, source);
    const reviewed = researchControl("Dato for kildevurdering"); reviewed.control.type="date";
    const reason = researchControl("Begrunnelse for kildevurdering", "textarea"); reason.control.maxLength=2000;
    result.append(reviewed.wrapper, reason.wrapper, researchButton("Registrer kildevurdering",()=>attempt(async()=> {
      if(!reason.control.value.trim() || !reviewed.control.value)throw new Error("Dato og begrunnelse kreves.");
      const response=await api("/api/research/source-reviews",{method:"POST",body:JSON.stringify({url,expected_revision:source.review_revision,reviewed_at:reviewed.control.value,note:reason.control.value.trim()})});
      if(current())researchJSON(result,response);
    })));
  })));
  for(const claim of site.content_document?.claims || []) if(claim.citations?.length) researchJSON(sourceSection,{claim_id:claim.id,citations:claim.citations});
  root.append(sourceSection);

  const history=researchSection("Revisjonshistorikk og ny korrigerende tekstendring"); history.dataset.detailSection="history";
  const historyList=document.createElement("div"), more=researchButton("Hent eldre historikk",()=>attempt(loadHistory));let cursor=null,loaded=false;
  async function loadHistory(){
    const response=await api(`/api/sites/${site.id}/history?limit=20${loaded && cursor ? `&before=${cursor}`:""}`);if(!current())return;
    if(!loaded)historyList.replaceChildren();loaded=true;cursor=response.next_cursor;
    for(const event of response.items){
      const item=document.createElement("article");researchText(item,`#${event.id} · ${event.event_type} · ${event.created_at}`);researchJSON(item,event.diff.length ? event.diff : event.payload);
      if(event.history_gap)researchText(item,"Fullt historisk øyeblikksbilde mangler; importkvitteringen beholdes.");
      if(event.event_type==="edit"){
        const side=researchSelect("Historisk side",[["before","Før"],["after","Etter"]]), field=researchSelect("Vanlig tekstfelt",[["name","Navn"],["short_rationale","Begrunnelse"]]);
        const reason=researchControl("Begrunnelse for ny endring","textarea");let prepared=null;
        const commit=researchButton("Lagre gjennomgått tekstendring",()=>attempt(async()=>{
          if(!prepared || !current())throw new Error("Gjennomgå et nytt utkast først.");
          await api(`/api/sites/${site.id}/history/compensate`,{method:"POST",body:JSON.stringify(prepared)});refresh();
        }));commit.disabled=true;
        [side.control,field.control,reason.control].forEach(control=>control.addEventListener("input",()=>{prepared=null;commit.disabled=true;}));
        item.append(side.wrapper,field.wrapper,reason.wrapper,researchButton("Forhåndsvis tekstendring",()=>attempt(async()=>{
          const body={expected_revision:site.revision,source_event_id:event.id,side:side.control.value,fields:[field.control.value],reason:reason.control.value.trim()};
          const effect=await api(`/api/sites/${site.id}/history/compensation-preview`,{method:"POST",body:JSON.stringify(body)});if(!current())return;
          prepared={...body,preview_hash:effect.preview_hash};researchJSON(item,effect);commit.disabled=false;
        })),commit);
      }
      historyList.append(item);
    }
    more.disabled=!cursor; researchText(historyList,"Full historikk fra import kan mangle; importkvitteringen beholdes.");
  }
  history.append(researchButton("Vis historikk",()=>attempt(loadHistory)),historyList,more);root.append(history);

  const fresh=researchSection("Aktualitet og ny vurdering av offentlig ankomst");fresh.dataset.detailSection="freshness";
  const reviewDate=researchControl("Vurderingsdato");reviewDate.control.type="date";
  const reviewReason=researchControl("Begrunnelse","textarea");reviewReason.control.maxLength=2000;
  const suspend=researchControl("Sett ankomsten på vent");suspend.control.type="checkbox";
  fresh.append(researchButton("Vis aktualitet",()=>attempt(async()=>{const result=await api(`/api/sites/${site.id}/freshness`);if(current())researchJSON(fresh,result);})),reviewDate.wrapper,reviewReason.wrapper,suspend.wrapper,researchButton("Registrer aktualitetsvurdering",()=>attempt(async()=>{
    await api(`/api/sites/${site.id}/freshness-review`,{method:"POST",body:JSON.stringify({expected_revision:site.revision,reviewed_at:reviewDate.control.value,reason:reviewReason.control.value.trim(),suspend_approach:suspend.control.checked})});refresh();
  })));root.append(fresh);

  const amendments=researchSection("Opprinnelige observasjoner og sporbare rettelser");amendments.dataset.detailSection="observation-amendments";
  for(const observation of site.field_observations || []){
    const item=document.createElement("article");researchText(item,`Observasjon #${observation.id} · revisjon ${observation.observation_revision ?? "ukjent"} · ${observation.withdrawn ? "trukket tilbake" : observation.outcome}`);
    researchText(item,"Opprinnelig innsendt vurdering");researchJSON(item,observation.original_snapshot);
    researchText(item,"Gjeldende vurdering, avgrensning og rettelser");researchJSON(item,{outcome:observation.outcome,note:observation.note,point_role:observation.point_role,uncertainty_m:observation.uncertainty_m,context:observation.context,amendments:observation.amendments,support_invalidated:observation.support_invalidated});
    if(observation.data_status!=="unavailable"){
      const changes=researchControl("Rettelse som JSON (for eksempel outcome, note, status eller context)","textarea");changes.control.maxLength=16000;
      const reason=researchControl("Begrunnelse for rettelsen","textarea");reason.control.maxLength=2000;let requestId=null,fingerprint=null;
      item.append(changes.wrapper,reason.wrapper,researchButton("Lagre ny rettelse",()=>attempt(async()=>{
        const edit=parseStrictJson(changes.control.value), reasonText=reason.control.value.trim();
        if(!reasonText || !edit || Array.isArray(edit) || typeof edit!=="object")throw new Error("Et rettelsesobjekt og en begrunnelse kreves.");
        if(["request_id","reason","expected_observation_revision"].some(key=>Object.hasOwn(edit,key)))throw new Error("Identitet og revisjon styres av skjemaet.");
        const signature=JSON.stringify([edit,reasonText]);if(signature!==fingerprint){requestId=crypto.randomUUID();fingerprint=signature;}
        await api(`/api/sites/${site.id}/observations/${observation.id}/amendments`,{method:"POST",body:JSON.stringify({...edit,request_id:requestId,reason:reasonText,expected_observation_revision:observation.observation_revision})});refresh();
      })));
    }
    amendments.append(item);
  }root.append(amendments);

  const seed=researchSection("Sammenlign ny grunntekst med kuratorens egne endringer");seed.dataset.detailSection="seed-reconciliation";
  seed.append(researchButton("Hent treveis sammenligning",()=>attempt(async()=>{
    const effect=await api(`/api/sites/${site.id}/seed-reconciliation`);if(!current())return;
    const result=document.createElement("div"), choices=new Map();seed.append(result);
    researchText(result,`Grunnversjon ${effect.base_seed_hash} · ny versjon ${effect.current_seed_hash}`);
    for(const item of effect.items){researchText(result,`${item.key} · ${item.conflict ? "motstrid" : "forslag"}`);researchJSON(result,item);
      const choice=researchSelect("Eksplisitt valg",[["","Velg versjon"],["local","Kuratorens versjon"],["current","Ny grunntekst"],["base","Opprinnelig grunntekst"]]);choices.set(item.key,choice.control);result.append(choice.wrapper);}
    const reason=researchControl("Begrunnelse for avstemming","textarea");result.append(reason.wrapper,researchButton("Lagre valgte versjoner",()=>attempt(async()=>{
      if([...choices.values()].some(control=>!control.value))throw new Error("Alle forslag må gjennomgås.");
      await api(`/api/sites/${site.id}/seed-reconciliation`,{method:"POST",body:JSON.stringify({expected_revision:effect.revision,preview_hash:effect.preview_hash,reason:reason.control.value.trim(),choices:Object.fromEntries([...choices].map(([key,control])=>[key,control.value]))})});refresh();
    })));
  })));root.append(seed);

  const assertions=researchSection("Støtte, motstrid og avledede påstander");assertions.dataset.detailSection="claim-assertions";
  const own=researchControl("Påstands-ID på dette stedet"),targetKey=researchControl("Motpartens eksterne nøkkel"),targetClaim=researchControl("Motpartens påstands-ID"),relation=researchSelect("Forhold",[["contradicts","Motstrid"],["supports","Støtter"],["derived_from","Avledet fra"]]),assertionReason=researchControl("Begrunnelse","textarea");
  assertions.append(own.wrapper,targetKey.wrapper,targetClaim.wrapper,relation.wrapper,assertionReason.wrapper,researchButton("Behold kildebasert påstandsforhold",()=>attempt(async()=>{
    const result=await api("/api/research/assertions",{method:"POST",body:JSON.stringify({external_key:site.external_key,claim_id:own.control.value.trim(),target_external_key:targetKey.control.value.trim(),target_claim_id:targetClaim.control.value.trim(),relation:relation.control.value,reason:assertionReason.control.value.trim()})});if(current())researchJSON(assertions,result);
  })),researchButton("Vis beholdte motstrid og støtteforhold",()=>attempt(async()=>{
    const records=await api(`/api/research/assertions?external_key=${encodeURIComponent(site.external_key)}`);if(!current())return;
    for(const record of records){const item=document.createElement("article");researchJSON(item,record);
      const decision=researchSelect("Ny vurdering",[["resolved","Behold som avklart"],["reopened","Åpne igjen"]]),reason=researchControl("Begrunnelse for vurderingen","textarea");item.append(decision.wrapper,reason.wrapper,researchButton("Registrer vurdering",()=>attempt(async()=>{
        const result=await api(`/api/research/assertions/${record.id}`,{method:"PATCH",body:JSON.stringify({expected_revision:record.revision,state:decision.control.value,reason:reason.control.value.trim()})});if(current())researchJSON(item,result);
      })));assertions.append(item);}
  })));root.append(assertions);
}
document.addEventListener("bunkerkartet:detail", event => { renderCuratorTools(event.detail.site,event.detail.root); });

async function renderCoverageAndRetention(site, root) {
  if (!hasSessionRole("owner")) return;
  const epoch=state.authEpoch,generation=state.detailGeneration;
  const current=()=>Boolean(state.token && state.authEpoch===epoch && state.detailGeneration===generation && root.isConnected);
  const status=document.createElement("p");status.setAttribute("role","status");
  const attempt=async action=>{try{await action();}catch(error){if(current()&&!isStaleRequest(error))status.textContent=error.message;}};
  const coverage=researchSection("Katalogdekning og uavklarte dimensjoner");coverage.dataset.detailSection="quality-coverage";
  researchText(coverage,"Identitet, plassering, adgang, aktualitet og kilder telles hver for seg. Kildeantall er ikke et mål på uavhengighet eller sannhet.");
  const dimension=researchSelect("Dimensjon",[["identity","Identitet"],["location","Plassering"],["access","Adgang"],["currentness","Aktualitet"],["evidence","Kildegrunnlag"],["source_dependence","Kildeavhengighet"],["unresolved_questions","Uavklarte spørsmål"]]);
  const result=document.createElement("div");let loaded=null;
  const membership=researchSelect("Dimensjonstilstand",[]);
  const refill=()=>{membership.control.replaceChildren();const counts=loaded?.dimensions?.[dimension.control.value]?.filtered || loaded?.[dimension.control.value==="source_dependence"?"source_dependence":"questions"]?.filtered || {};for(const key of Object.keys(counts).filter(key=>dimension.control.value!=="unresolved_questions" || ["present","none","unavailable"].includes(key))){const option=document.createElement("option");option.value=key;option.textContent=`${key}: ${counts[key]}`;membership.control.append(option);}};
  dimension.control.addEventListener("change",refill);
  coverage.append(researchButton("Vis katalogdekning",()=>attempt(async()=>{const data=await api('/api/quality/coverage');if(!current())return;loaded=data;result.replaceChildren();researchJSON(result,data);refill();})),dimension.wrapper,membership.wrapper,researchButton("Vis stedene i valgt dimensjon",()=>attempt(async()=>{
    const data=await api(`/api/quality/coverage?dimension=${encodeURIComponent(dimension.control.value)}&state=${encodeURIComponent(membership.control.value)}`);if(!current())return;result.replaceChildren();researchJSON(result,data);
    for(const item of data.drilldown.items)result.append(researchButton(`Åpne ${item.name}`,()=>openDetail(item.site_id)));
  })),result);root.append(coverage);
  const retention=researchSection("Eksakt sletting av private ruter");retention.dataset.detailSection="route-retention";
  researchText(retention,"Bare uttrykkelig valgte ruter slettes. Stedskilder beholdes. Kopierte besøksplaner, eksportfiler og eksisterende sikkerhetskopier har egne beholdningsregler.");
  const ids=researchControl("Rute-ID-er som skal slettes, kommaseparert"),reason=researchControl("Begrunnelse for rutesletting","textarea");reason.control.maxLength=500;let prepared=null;
  const output=document.createElement("div");
  const commit=researchButton("Bekreft sletting av de viste rutene",()=>attempt(async()=>{if(!prepared)throw new Error("Gjennomgå en ny forhåndsvisning først.");const response=await api('/api/admin/retention/routes/commit',{method:'POST',body:JSON.stringify(prepared)});if(current()){researchJSON(output,response);prepared=null;commit.disabled=true;}}));commit.disabled=true;
  [ids.control,reason.control].forEach(control=>control.addEventListener('input',()=>{prepared=null;commit.disabled=true;output.replaceChildren();}));
  retention.append(ids.wrapper,reason.wrapper,researchButton("Forhåndsvis nøyaktig rutesletting",()=>attempt(async()=>{const selected=ids.control.value.split(',').map(value=>Number(value.trim()));if(selected.some(value=>!Number.isInteger(value)||value<=0)||!reason.control.value.trim())throw new Error("Velg positive rute-ID-er og oppgi en begrunnelse.");const body={route_ids:selected,reason:reason.control.value.trim()};const response=await api('/api/admin/retention/routes/preview',{method:'POST',body:JSON.stringify(body)});if(current()){output.replaceChildren();researchJSON(output,response);prepared={...body,preview_hash:response.preview_hash};commit.disabled=false;}})),output,commit,researchButton("Vis beholdningskvitteringer",()=>attempt(async()=>{const data=await api('/api/admin/retention/routes/receipts');if(current())researchJSON(output,data);})),status);root.append(retention);
}
document.addEventListener("bunkerkartet:detail",event=>renderCoverageAndRetention(event.detail.site,event.detail.root));
