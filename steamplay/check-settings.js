// The exact production Settings script, emitted by the CI driver, runs against
// a JSX/DOM bridge. This verifies persistence commands and visible failures.
const fs = require('fs'), vm = require('vm'), assert = require('assert/strict');
const context = {SteamClient:{Apps:{SpecifyCompatTool: async (...args) => { context.calls.push(args); if(context.fail)throw Error('save failed');return true; }}},calls:[]};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const settings = context.__GLUON_SETTINGS__;
// Use the exact generated overview getter; raw invalid OS flags are true in
// these cases, so a mistaken clear would hide a real unsupported/native state.
const overview=vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),context);
context.__NOTPROTON_NATIVE_PLATFORM__={mac:new Set([32,64]),windows:new Set([11,32,64]),mac32:new Set([32])};
let details;
context.appDetailsStore={GetAppDetails:()=>details};
const invalid=(id,chosen,priority,enabled=true)=>{
 details=chosen===undefined?undefined:{strCompatToolName:chosen,nCompatToolPriority:priority};
 settings.effectiveMac32=enabled;
 return Object.getOwnPropertyDescriptor(overview,'is_invalid_os_type').get.call({appid:id,most_available_per_client_data:{is_invalid_os_type:true}});
};
assert.equal(invalid(11,'',0,false),false,'unmapped Windows-only defaults remain usable with an empty name');
assert.equal(invalid(11,undefined,undefined,false),false,'Windows default does not require AppDetails to contain Gluon');
assert.equal(invalid(32,'gluon.none',undefined),true,'explicit None blocks Mac32 automatic UI availability');
assert.equal(invalid(32,'',250),true,'an unavailable explicit tool with empty resolved name remains blocked');
assert.equal(invalid(32,'other-tool',250),true,'explicit other tool is not presented as Gluon');
assert.equal(invalid(32,'gluon-proton',250,false),false,'explicit Gluon remains usable when global automatic policy is off');
assert.equal(invalid(32,'',0),false,'confirmed Mac32 uses the enabled automatic policy');
assert.equal(invalid(32,'',0,false),true,'disabled Mac32 automatic policy preserves unsupported state');
assert.equal(invalid(64,'',0),true,'Mac64 is never made eligible by the Mac32 policy');
assert.equal(invalid(11,'gluon.none',250),true,'Windows explicit None remains respected');
settings.effectiveMac32=false;

const jsx=(type,props)=>({type,...props});
const page = settings.render(jsx), label=page.children[0], input=label.children[0];
assert.equal(input.role,'switch');assert.equal(input.defaultChecked,false);
assert.equal(label.children[1].children,'Use Gluon for 32-bit Mac games');
const status={textContent:''};
const target={checked:true,disabled:false,closest:()=>({querySelector:()=>status})};
(async()=>{
 await input.onChange({currentTarget:target});
 assert.equal(context.calls[0][0],0);assert.equal(context.calls[0][1],'__gluon_settings_mac32_on_v1');
 assert.equal(settings.autoMac32,true);assert.equal(target.disabled,false);assert.match(status.textContent,/Restart Steam/);assert.equal(settings.effectiveMac32,false);
 context.fail=true;target.checked=false;
 await input.onChange({currentTarget:target});
 assert.equal(target.checked,true);assert.equal(settings.autoMac32,true);assert.match(status.textContent,/could not be saved/);
 // Later chunks must keep the live setting rather than resetting it to the old
 // startup snapshot.
 vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
 assert.equal(settings.autoMac32,true);
 // A newer Steam page supplies its own CompatManager query setter. Use it
 // when the older SteamClient.Apps API is absent.
 const nativeCalls=[];
 settings.specify=async name=>{nativeCalls.push(name);return true};
 context.SteamClient=undefined;target.checked=false;
 await input.onChange({currentTarget:target});
 assert.equal(nativeCalls[0],'__gluon_settings_mac32_off_v1');assert.equal(settings.autoMac32,false);
 console.log('Gluon Settings switch, native bridge command and failure recovery passed');
})().catch(e=>{console.error(e);process.exitCode=1});
