#ifndef GLUON_SETTINGS_JAVASCRIPT_H
#define GLUON_SETTINGS_JAVASCRIPT_H
// Keep UI availability aligned with native explicit choices. AppDetails may
// report an empty resolved name for an unavailable chosen tool, so app-priority
// entries take precedence even when their name is empty. Windows-only defaults
// must remain usable without relying on an explicit Gluon mapping being present.
#define GLUON_INVALID_OS_GETTER \
    "get is_invalid_os_type(){const bad=this.most_available_per_client_data.is_invalid_os_type;if(!bad)return false;" \
    "const p=globalThis.__NOTPROTON_NATIVE_PLATFORM__,d=globalThis.appDetailsStore?.GetAppDetails(this.appid)," \
    "n=d?.strCompatToolName||\"\",gluon=/^(gluon|gluon-proton)$/.test(n),explicit=d?.nCompatToolPriority>=250;" \
    "if(n===\"gluon.none\"||explicit&&!gluon)return true;if(gluon)return false;if(n)return true;" \
    "if(p?.windows?.has(this.appid)&&!p.mac?.has(this.appid))return false;" \
    "return !(globalThis.__GLUON_SETTINGS__?.effectiveMac32&&p?.mac32?.has(this.appid))}"

// An actual Settings section, using the page's JSX runtime and an accessible
// switch. The native command never writes a Steam compatibility mapping.
#define GLUON_SETTINGS_SCRIPT \
    "globalThis.__GLUON_SETTINGS__.render=J=>J(\"div\",{style:{padding:\"12px 0\"},children:[" \
    "J(\"label\",{style:{display:\"flex\",alignItems:\"center\",gap:12,cursor:\"pointer\"},children:[" \
    "J(\"input\",{type:\"checkbox\",role:\"switch\",name:\"gluon_auto_mac32\",defaultChecked:globalThis.__GLUON_SETTINGS__.autoMac32," \
    "onChange:async e=>{const t=e.currentTarget,v=t.checked,s=t.closest(\"div\").querySelector(\"[role=status]\");" \
    "t.disabled=true;try{const f=globalThis.__GLUON_SETTINGS__.specify||(typeof SteamClient?.Apps?.SpecifyCompatTool===\"function\"?n=>SteamClient.Apps.SpecifyCompatTool(0,n):null);if(!f)throw Error();" \
    "const r=await f(v?\"__gluon_settings_mac32_on_v1\":\"__gluon_settings_mac32_off_v1\");" \
    "if(r===false||r?.success===false)throw Error();globalThis.__GLUON_SETTINGS__.autoMac32=v;s.textContent=v===globalThis.__GLUON_SETTINGS__.effectiveMac32?\"\":\"Restart Steam to apply this setting.\"}" \
    "catch{t.checked=!v;s.textContent=\"The setting could not be saved. Please try again.\"}finally{t.disabled=false}}})," \
    "J(\"span\",{children:\"Use Gluon for 32-bit Mac games\"})]})," \
    "J(\"p\",{style:{opacity:.75,lineHeight:1.5,margin:\"10px 0 0 28px\"},children:" \
    "\"Automatically use the Windows version when a game's Mac version is 32-bit. 64-bit Mac games keep their native version.\"})," \
    "J(\"p\",{role:\"status\",\"aria-live\":\"polite\",style:{marginLeft:28},children:globalThis.__GLUON_SETTINGS__.autoMac32===globalThis.__GLUON_SETTINGS__.effectiveMac32?\"\":\"Restart Steam to apply this setting.\"})]});\n"
#endif
