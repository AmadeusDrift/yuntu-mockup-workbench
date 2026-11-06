const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/main.cjs','utf8').split('async function installUpdate() {')[1].split('\nfunction api(')[0];
function harness(task,verify=async()=>'/installer.exe'){
 let spawned=0,stopped=0;
 const updater={busy:false,state:{status:'ready'},verifiedInstaller:verify,set(s){this.state=s}};
 const context={updater,installInFlight:false,closing:false,exiting:false,app:{getPath:()=>'/program/app.exe',quit(){}},dataRoot:'/profile',path:require('node:path'),window:{destroy(){}},dialog:{showMessageBox:async()=>({response:0}),showErrorBox(){}},api:async(route)=>route==='/api/control'?{}:{task:task()},stopBackend:async()=>{stopped++},spawn:()=>{spawned++;return{once(event,fn){if(event==='spawn')queueMicrotask(fn)},unref(){}}},setTimeout};
 vm.createContext(context);vm.runInContext('async function installUpdate() {'+source,context);
 return {context,updater,run:()=>context.installUpdate(),counts:()=>({spawned,stopped})};
}
test('concurrent installation requests execute one installer',async()=>{
 let release;const gate=new Promise(r=>release=r);const h=harness(()=>({status:'idle'}),async()=>{await gate;return '/installer.exe'});
 const first=h.run(),second=h.run();release();await Promise.all([first,second]);assert.deepEqual(h.counts(),{spawned:1,stopped:1});
});
test('failed task checkpoint keeps backend running and permits retry',async()=>{
 let reads=0;const h=harness(()=>++reads===1?{status:'running'}:{status:'failed',error:'disk full'});
 await h.run();assert.deepEqual(h.counts(),{spawned:0,stopped:0});assert.match(h.updater.state.message,/disk full/);assert.equal(h.context.installInFlight,false);
});
