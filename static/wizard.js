/* ── GGPlay Wizard v4 — Excel-First Autopilot ── */
var state={step:1,company:{},details:{},branding:{},domain:"",email:"",duns:{number:"",status:""}};
var SIC_ARCHETYPE={"78200":"shift","88100":"shift","87100":"shift","53202":"shift","49410":"shift","98000":"shift"};
var STRIP_SUFFIXES=/\s+(LTD|LIMITED|LLP|PLC|INC|CORP|CO|COMPANY)\.?\s*$/i;
var FW={};["management","services","solutions","group","holdings","international","personnel","consulting","associates","partners","enterprises","agency","properties","trading","recruitment","staffing","resources","global","uk","consultants","advisors","advisory","professional","facilities","operations","logistics","industries","commercial","ventures","capital","investments","developments","construction","contractors","maintenance","care","healthcare","health","medical","nursing","education","training","academy","institute","foundation","technology","technologies","tech","digital","systems","network","networks","communications","media","road","limited","ltd"].forEach(function(w){FW[w]=1;});

function generateShortName(c){var n=c.trim().replace(STRIP_SUFFIXES,"").replace(/\s*-\s*/g,"-").replace(/\s+/g," ").trim().replace(/^\d+\s+/,"").replace(/^(ST|SAINT)\s+/i,"St ");var w=n.split(/\s+/),m=w.filter(function(x){return!FW[x.toLowerCase()];});if(!m.length)m=w.slice(0,2);var r="";for(var i=0;i<Math.min(m.length,3);i++){var c2=r?r+" "+m[i]:m[i];if(c2.length>20&&r)break;r=c2;}return r.toLowerCase().replace(/(?:^|\s|-)\S/g,function(c3){return c3.toUpperCase();}).replace(/^-+|-+$/g,"").trim().substring(0,30)||"App";}

function hashToHue(n){var h=2166136261;for(var i=0;i<n.length;i++){h^=n.charCodeAt(i);h=Math.imul(h,16777619);}return((h>>>0)%360)/360;}
function hlsToRgb(h,l,s){if(s===0)return[l,l,l];function hue2rgb(p,q,t){if(t<0)t+=1;if(t>1)t-=1;if(t<1/6)return p+(q-p)*6*t;if(t<1/2)return q;if(t<2/3)return p+(q-p)*(2/3-t)*6;return p;}var q=l<.5?l*(1+s):l+s-l*s,p=2*l-q;return[hue2rgb(p,q,h+1/3),hue2rgb(p,q,h),hue2rgb(p,q,h-1/3)];}
function rgbHex(r,g,b){function x(v){return Math.round(v*255).toString(16).padStart(2,"0");}return"#"+x(r)+x(g)+x(b);}
function paletteFor(n){var h=hashToHue(n.toUpperCase().trim()),pr=hlsToRgb(h,.32,.65),dr=hlsToRgb(h,.22,.70),ar=hlsToRgb((h+30/360)%1,.48,.72);return{primary:rgbHex(pr[0],pr[1],pr[2]),primary_dark:rgbHex(dr[0],dr[1],dr[2]),accent:rgbHex(ar[0],ar[1],ar[2])};}

function $(s){return document.querySelector(s);}function $$(s){return document.querySelectorAll(s);}
function show(el,cls){el.classList.remove("hidden");if(cls)el.className=el.className.replace(/\b(info|success|error|warning)\b/g,"").trim()+" "+cls;}
function hide(el){el.classList.add("hidden");}
function initials(n){var p=n.replace(/-/g," ").split(/\s+/).filter(Boolean);if(!p.length)return"?";if(p.length===1)return p[0].substring(0,2).toUpperCase();return(p[0][0]+p[1][0]).toUpperCase();}
function slugify(n){return n.toLowerCase().replace(/[^a-z0-9]+/g,"").substring(0,30);}
function autoDisplayName(cn){return cn.trim().replace(STRIP_SUFFIXES,"").split(/\s+/).map(function(w){return w.charAt(0).toUpperCase()+w.slice(1).toLowerCase();}).join(" ").substring(0,30).trimEnd();}
function delay(ms){return new Promise(function(r){setTimeout(r,ms);});}

function manualPayload(companyNumber){
  return {
    company_number: companyNumber,
    representative_id: {
      full_name_as_on_id: $("#manualRepFullName").value.trim(),
      personal_address: $("#manualRepAddress").value.trim(),
      id_type: $("#manualIdType").value.trim(),
      id_country: $("#manualIdCountry").value.trim(),
      id_expiry_date: $("#manualIdExpiry").value,
      id_image_front: $("#manualIdFront").value.trim(),
      id_image_back: $("#manualIdBack").value.trim(),
      id_quality_check: $("#manualIdQuality").value.trim() || "pending"
    }
  };
}

function fillManualForm(data){
  var rep=data.representative_id||{};
  $("#manualRepFullName").value=rep.full_name_as_on_id||"";
  $("#manualRepAddress").value=rep.personal_address||"";
  $("#manualIdType").value=rep.id_type||"";
  $("#manualIdCountry").value=rep.id_country||"";
  $("#manualIdExpiry").value=rep.id_expiry_date||"";
  $("#manualIdFront").value=rep.id_image_front||"";
  $("#manualIdBack").value=rep.id_image_back||"";
  $("#manualIdQuality").value=rep.id_quality_check||"";
}

function loadManualInputs(companyNumber){
  if(!companyNumber)return Promise.resolve();
  var st=$("#manualInputsStatus");
  show(st,"info");st.textContent="Loading manual inputs...";
  return fetch("/api/pipeline/manual-inputs/"+encodeURIComponent(companyNumber))
    .then(function(r){return r.json();})
    .then(function(data){
      if(data&&Object.keys(data).length){
        fillManualForm(data);
        st.textContent="Manual inputs loaded for "+companyNumber+".";
        show(st,"success");
      }else{
        st.textContent="No saved manual inputs yet for "+companyNumber+".";
        show(st,"warning");
      }
    })
    .catch(function(){st.textContent="Could not load manual inputs.";show(st,"error");});
}

function saveManualInputs(){
  var companyNumber=$("#dCompanyNumber").value.trim();
  var st=$("#manualInputsStatus");
  if(!companyNumber){show(st,"warning");st.textContent="Company Number is required.";return;}
  var payload=manualPayload(companyNumber);
  show(st,"info");st.textContent="Saving manual inputs...";
  fetch("/api/pipeline/manual-inputs/"+encodeURIComponent(companyNumber),{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify(payload)
  }).then(function(r){return r.json();}).then(function(d){
    if(d.success){st.textContent="Manual inputs saved.";show(st,"success");}
    else{st.textContent="Error: "+(d.error||"Unknown");show(st,"error");}
  }).catch(function(){st.textContent="Failed to save manual inputs.";show(st,"error");});
}

// ─── Navigation ──────────────────────────────────────────────
function goStep(n){
  state.step=n;$$(".panel").forEach(function(p){p.classList.remove("active");});
  var t=$("#panel-"+n);if(t)t.classList.add("active");
  $$(".stepper .step").forEach(function(s){var sn=parseInt(s.dataset.step);s.classList.remove("active","done");if(sn===n)s.classList.add("active");else if(sn<n)s.classList.add("done");});
  if(n===6)populateReview();
}
window.goStep=goStep;
function markStepDone(n){$$(".stepper .step").forEach(function(s){var sn=parseInt(s.dataset.step);if(sn===n){s.classList.remove("active");s.classList.add("done");}else if(sn===n+1){s.classList.add("active");}});}

// ─── Autopilot UI ────────────────────────────────────────────
var apLog=$("#autopilotLog"),apSection=$("#autopilotSection"),apProgress=$("#apProgressBar");
function logAP(icon,text,status){var e=document.createElement("div");e.className="ap-entry "+(status||"ok");e.innerHTML='<span class="ap-icon">'+icon+'</span><span class="ap-text">'+text+'</span>';apLog.appendChild(e);apLog.scrollTop=apLog.scrollHeight;return e;}
function updateEntry(e,icon,text,status){e.className="ap-entry "+(status||"ok");e.innerHTML='<span class="ap-icon">'+icon+'</span><span class="ap-text">'+text+'</span>';}
function setProgress(pct){if(apProgress)apProgress.style.width=pct+"%";}

// ═══════════════════════════════════════════════════════════════
// LOAD FROM EXCEL — primary data source
// ═══════════════════════════════════════════════════════════════
var tbody=$("#companyTable").querySelector("tbody");
var loadStatus=$("#loadStatus");

function loadFromExcel(){
  show(loadStatus,"info"); loadStatus.textContent="Loading companies from pipeline Excel\u2026";
  fetch("/api/pipeline/list").then(function(r){return r.json();}).then(function(companies){
    if(!companies.length){loadStatus.textContent="No companies in Excel yet. Use Search to add.";show(loadStatus,"warning");return;}
    tbody.innerHTML="";
    companies.forEach(function(c,i){
      var cn=c["Company Number"]||"";
      var name=c["Company Name"]||"";
      var shortName=c["Short Name"]||"";
      var domain=c["Domain"]||"";
      var email=c["Assigned Email"]||"";
      var duns=c["DUNS Number"]||"";
      var hasShort=shortName?"<span class='tag ok'>"+shortName+"</span>":"<span class='tag warn'>Missing</span>";
      var hasDomain=domain?"<span class='tag ok'>"+domain+"</span>":"<span class='tag warn'>Missing</span>";
      var hasDuns=duns?"<span class='tag ok'>"+duns+"</span>":"<span class='tag warn'>Missing</span>";

      var tr=document.createElement("tr");
      tr.innerHTML=
        '<td><code>'+cn+'</code></td>'+
        '<td><strong>'+name+'</strong></td>'+
        '<td>'+hasShort+'</td>'+
        '<td>'+hasDomain+'</td>'+
        '<td>'+hasDuns+'</td>'+
        '<td><button class="btn primary sm">\u26A1 Onboard</button></td>';

      (function(companyRow){
        tr.querySelector("button").addEventListener("click",function(ev){
          ev.stopPropagation();
          tbody.querySelectorAll("button").forEach(function(b){b.disabled=true;b.textContent="\u2026";});
          ev.target.textContent="\u2713";ev.target.className="btn success sm";
          autopilotFromExcel(companyRow);
        });
      })(c);
      tbody.appendChild(tr);
    });
    loadStatus.textContent=companies.length+" companies loaded. Click \u26A1 Onboard to auto-fill missing data.";
    show(loadStatus,"success");
  }).catch(function(err){
    loadStatus.textContent="Could not load Excel. Is the server running?";
    show(loadStatus,"error");
  });
}

// ═══════════════════════════════════════════════════════════════
// AUTOPILOT — from Excel data (no CH search needed)
// ═══════════════════════════════════════════════════════════════
async function autopilotFromExcel(row){
  show(apSection);apLog.innerHTML="";setProgress(0);markStepDone(1);

  var cn=String(row["Company Number"]||"").trim();
  var name=row["Company Name"]||"";
  var sic=row["SIC Codes"]||"";
  var address=row["Address"]||"";
  var directors=row["Directors"]||"";
  var nationalities=row["Director Nationalities"]||"";
  var existingDomain=row["Domain"]||"";
  var existingEmail=row["Assigned Email"]||"";
  var existingDuns=row["DUNS Number"]||"";
  var existingShort=row["Short Name"]||"";
  var status=row["Status"]||"";
  var type=row["Type"]||"";
  var created=row["Date of Creation"]||"";

  // ── Step 2: Fill details from Excel ──
  var e1=logAP("\u2705","<strong>"+name+"</strong> \u2014 loaded from Excel","ok");
  $("#dCompanyNumber").value=cn;
  $("#dCompanyName").value=name;
  $("#dStatus").value=status;
  $("#dType").value=type;
  $("#dCreated").value=created;
  $("#dSIC").value=sic;
  $("#dAddress").value=address;
  $("#dDirectors").value=directors;
  $("#dNationalities").value=nationalities;
  markStepDone(2);setProgress(25);await delay(200);

  // ── Step 3: Short Name + Branding ──
  var shortName=existingShort||generateShortName(name);
  var pal=paletteFor(name);
  $("#bShortName").value=shortName;
  $("#bAutoName").value=autoDisplayName(name);
  $("#bAppId").value="uk.c"+cn+".shift";
  $("#bArchetype").value=SIC_ARCHETYPE[sic.split(",")[0].trim()]||"shift";
  state.branding={short_name:shortName,palette:pal,display_name:shortName};
  fillBrandPreview();
  logAP("\u2705",'App: <strong>'+shortName+'</strong> \u00B7 <span class="dot" style="background:'+pal.primary+'"></span> <span class="dot" style="background:'+pal.accent+'"></span>'+(existingShort?" (from Excel)":" (auto-generated)"),"ok");
  markStepDone(3);setProgress(45);await delay(200);

  // ── Step 4: Domain & Email ──
  var domain=existingDomain;
  if(!domain){
    var slug=slugify(shortName);
    domain=slug+".co.uk";
    var e3=logAP("\u23F3","Checking domain "+domain+"\u2026","pending");
    try{
      var cr=await fetch("/api/domains/check?domains="+encodeURIComponent([slug+".co.uk",slug+".uk",slug+"app.co.uk"].join(","))).then(function(r){return r.json();});
      var rs=Array.isArray(cr)?cr:(cr.results||[]);
      var av=rs.find(function(r){return r.available;});
      if(av){domain=av.domain;updateEntry(e3,"\u2705","Domain: <strong>"+domain+"</strong> (available)","ok");}
      else{updateEntry(e3,"\u26A0\uFE0F","Domain: <strong>"+domain+"</strong> (verify manually)","warn");}
    }catch(err){updateEntry(e3,"\u26A0\uFE0F","Domain: <strong>"+domain+"</strong> (API offline)","warn");}
  } else {
    logAP("\u2705","Domain: <strong>"+domain+"</strong> (from Excel)","ok");
  }
  $("#domainInput").value=domain;
  $("#supportEmail").value="support@"+domain;
  state.domain=domain;
  setProgress(55);

  var email=existingEmail;
  if(!email){
    var e4=logAP("\u23F3","Assigning email\u2026","pending");
    try{
      var pr=await fetch("/api/email-pool/status").then(function(r){return r.json();});
      if(pr.available_emails&&pr.available_emails.length>0){
        email=pr.available_emails[0];
        var sel=$("#emailSelect");sel.innerHTML='<option value="">\u2014 '+pr.available+' available \u2014</option>';
        pr.available_emails.forEach(function(e){var o=document.createElement("option");o.value=e;o.textContent=e;sel.appendChild(o);});
        sel.value=email;
        updateEntry(e4,"\u2705","Email: <strong>"+email+"</strong> (from pool)","ok");
      }else{updateEntry(e4,"\u26A0\uFE0F","No emails in pool","warn");}
    }catch(err){updateEntry(e4,"\u26A0\uFE0F","Email pool offline","warn");}
  } else {
    logAP("\u2705","Email: <strong>"+email+"</strong> (from Excel)","ok");
    $("#emailSelect").innerHTML='<option value="'+email+'">'+email+'</option>';
    $("#emailSelect").value=email;
  }
  state.email=email;
  markStepDone(4);setProgress(75);await delay(200);

  // ── Step 5: DUNS ──
  if(directors){
    var pts=directors.split("\n")[0].split(",").map(function(s){return s.trim();});
    if(pts.length>=2){$("#dunsFirst").value=pts[1].split(/\s+/)[0]||"";$("#dunsLast").value=pts[0].charAt(0)+pts[0].slice(1).toLowerCase();}
  }
  if(email)$("#dunsEmail").value=email;

  if(existingDuns){
    logAP("\u2705","DUNS: <strong>"+existingDuns+"</strong> (from Excel)","ok");
    $("#dunsNumber").value=existingDuns;
    state.duns={number:existingDuns,status:"found"};
  } else {
    var e5=logAP("\u23F3","Looking up DUNS\u2026","pending");
    try{
      var dr=await fetch("/api/duns/lookup?company_number="+cn).then(function(r){return r.json();});
      if(dr.duns_number){$("#dunsNumber").value=dr.duns_number;state.duns={number:dr.duns_number,status:"found"};updateEntry(e5,"\u2705","DUNS: <strong>"+dr.duns_number+"</strong>","ok");}
      else{state.duns.status="not_found";updateEntry(e5,"\u26A0\uFE0F","DUNS not found \u2014 request later","warn");}
    }catch(err){updateEntry(e5,"\u26A0\uFE0F","DUNS lookup offline","warn");}
  }
  markStepDone(5);setProgress(95);await delay(300);

  setProgress(100);logAP("\uD83C\uDF89","<strong>Done! Opening review\u2026</strong>","ok");
  await delay(600);
  goStep(6);
  loadManualInputs(cn);
}

// ─── Brand preview ───────────────────────────────────────────
function fillBrandPreview(){var cn=$("#dCompanyName").value||"",dn=$("#bShortName").value||generateShortName(cn),pal=paletteFor(cn);
  $("#swPrimary").style.background=pal.primary;$("#swPrimaryDark").style.background=pal.primary_dark;$("#swAccent").style.background=pal.accent;
  $("#fakeIcon").style.background=pal.primary;$("#iconInitials").textContent=initials(dn);$("#iconLabel").textContent=dn;$("#bNameCount").textContent=($("#bShortName").value||"").length+" / 30";}
$("#bShortName").addEventListener("input",function(){$("#bNameCount").textContent=this.value.length+" / 30";fillBrandPreview();});
$("#domainInput").addEventListener("input",function(){$("#supportEmail").value=this.value.trim()?"support@"+this.value.trim():"";});

// ─── Manual buttons ──────────────────────────────────────────
$("#btnCheckDomain").addEventListener("click",function(){var d=$("#domainInput").value.trim();if(!d)return;show($("#domainStatus"),"info");$("#domainStatus").textContent="Checking\u2026";fetch("/api/domains/check?domains="+encodeURIComponent(d)).then(function(r){return r.json();}).then(function(data){if(data.error){$("#domainStatus").textContent="Error";show($("#domainStatus"),"error");return;}var rs=Array.isArray(data)?data:(data.results||[data]);var av=rs.find(function(r){return r.available;});if(av){$("#domainStatus").textContent="\u2705 Available!";show($("#domainStatus"),"success");}else{$("#domainStatus").textContent="\u274C Not available.";show($("#domainStatus"),"warning");}}).catch(function(){$("#domainStatus").textContent="Failed.";show($("#domainStatus"),"error");});});
$("#btnDunsLookup").addEventListener("click",function(){var cn=$("#dCompanyNumber").value;if(!cn)return;show($("#dunsStatus"),"info");$("#dunsStatus").textContent="Looking up\u2026";fetch("/api/duns/lookup?company_number="+cn).then(function(r){return r.json();}).then(function(d){if(d.duns_number){$("#dunsNumber").value=d.duns_number;$("#dunsStatus").textContent="\u2705 "+d.duns_number;show($("#dunsStatus"),"success");}else{$("#dunsStatus").textContent="Not found.";show($("#dunsStatus"),"warning");}}).catch(function(){$("#dunsStatus").textContent="Failed.";show($("#dunsStatus"),"error");});});
$("#btnDunsRequest").addEventListener("click",function(){var cn=$("#dCompanyNumber").value,em=$("#dunsEmail").value;if(!cn||!em){show($("#dunsStatus"),"warning");$("#dunsStatus").textContent="Need company # + email.";return;}show($("#dunsStatus"),"info");$("#dunsStatus").textContent="Submitting\u2026";fetch("/api/duns/request",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({company_number:cn,email:em,first_name:$("#dunsFirst").value,last_name:$("#dunsLast").value})}).then(function(r){return r.json();}).then(function(d){if(d.duns_number){$("#dunsNumber").value=d.duns_number;$("#dunsStatus").textContent="\u2705 "+d.duns_number;show($("#dunsStatus"),"success");}else{$("#dunsStatus").textContent=d.message||"Submitted.";show($("#dunsStatus"),"success");}}).catch(function(){$("#dunsStatus").textContent="Failed.";show($("#dunsStatus"),"error");});});
$("#btnApplyGoogleTxt").addEventListener("click",function(){var domain=$("#domainInput").value.trim(),token=$("#googleTxtToken").value.trim(),host=$("#googleTxtHost").value.trim()||"@",cn=$("#dCompanyNumber").value.trim(),st=$("#googleTxtStatus");if(!domain){show(st,"warning");st.textContent="Domain is required.";return;}if(!token){show(st,"warning");st.textContent="Paste the Google TXT token first.";return;}show(st,"info");st.textContent="Applying TXT to DNS...";fetch("/api/domains/apply-google-txt",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({company_number:cn,domain:domain,txt_value:token,hostname:host})}).then(function(r){return r.json();}).then(function(d){if(d.success){st.textContent="\u2705 TXT configured for "+domain+" (host "+host+")";show(st,"success");}else{st.textContent="Error: "+(d.error||"Failed to apply TXT");show(st,"error");}}).catch(function(){st.textContent="Failed to apply TXT.";show(st,"error");});});
$("#btnLoadManualInputs").addEventListener("click",function(){loadManualInputs($("#dCompanyNumber").value.trim());});
$("#btnSaveManualInputs").addEventListener("click",saveManualInputs);

// ─── Review ──────────────────────────────────────────────────
function populateReview(){var cn=$("#dCompanyNumber").value,nm=$("#dCompanyName").value,sn=$("#bShortName").value,dn=sn||generateShortName(nm),dm=$("#domainInput").value,em=$("#emailSelect").value||$("#emailManual").value,sp=$("#supportEmail").value,du=$("#dunsNumber").value,pal=paletteFor(nm),w='<span class="tag warn">Not set</span>';
  var rows=[["Company #",cn],["Company Name",nm],["App Name","<strong>"+dn+"</strong>"],["Application ID","<code>uk.c"+cn+".shift</code>"],["Archetype",$("#bArchetype").value],["SIC Codes",$("#dSIC").value],["Address",$("#dAddress").value],["Domain",dm||w],["Developer Email",em||w],["Support Email",sp||w],["DUNS",du||'<span class="tag warn">Pending</span>'],["Palette",'<span class="dot" style="background:'+pal.primary+'"></span> '+pal.primary+' &nbsp;<span class="dot" style="background:'+pal.primary_dark+'"></span> '+pal.primary_dark+' &nbsp;<span class="dot" style="background:'+pal.accent+'"></span> '+pal.accent]];
  $("#reviewTable tbody").innerHTML=rows.map(function(r){return'<tr><td>'+r[0]+'</td><td>'+r[1]+'</td></tr>';}).join("");}

// ─── Submit ──────────────────────────────────────────────────
var btnSubmit=$("#btnSubmit"),submitStatus=$("#submitStatus");
btnSubmit.addEventListener("click",function(){var p={company_number:$("#dCompanyNumber").value,company_name:$("#dCompanyName").value,short_name:$("#bShortName").value,sic_codes:$("#dSIC").value,address:$("#dAddress").value,domain:$("#domainInput").value,email:$("#emailSelect").value||$("#emailManual").value,support_email:$("#supportEmail").value,duns_number:$("#dunsNumber").value,duns_email:$("#dunsEmail").value,archetype:$("#bArchetype").value,directors:$("#dDirectors").value,nationalities:$("#dNationalities").value,status:$("#dStatus").value,type:$("#dType").value,date_of_creation:$("#dCreated").value};
  btnSubmit.disabled=true;show(submitStatus,"info");submitStatus.textContent="Saving\u2026";
  fetch("/api/pipeline/add",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)}).then(function(r){return r.json();}).then(function(d){if(d.success){submitStatus.innerHTML='\u2705 <strong>Saved!</strong> Row '+(d.row||"")+'. <button class="btn primary sm" onclick="resetWizard()" style="margin-left:.5rem">\u26A1 Next Company</button>';show(submitStatus,"success");}else{submitStatus.textContent="Error: "+(d.error||"Unknown");show(submitStatus,"error");btnSubmit.disabled=false;}}).catch(function(){submitStatus.textContent="Connection failed.";show(submitStatus,"error");btnSubmit.disabled=false;});});

// ─── Reset ───────────────────────────────────────────────────
function resetWizard(){state={step:1,company:{},details:{},branding:{},domain:"",email:"",duns:{number:"",status:""}};$$('input:not([type=hidden]),select,textarea').forEach(function(el){if(el.type==="checkbox")el.checked=false;else el.value="";});hide(apSection);hide(submitStatus);hide($("#manualInputsStatus"));btnSubmit.disabled=false;setProgress(0);goStep(1);loadFromExcel();}
window.resetWizard=resetWizard;

// ─── Init ────────────────────────────────────────────────────
document.getElementById("footYear").textContent=new Date().getFullYear();
loadFromExcel();
