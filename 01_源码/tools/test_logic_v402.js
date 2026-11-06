// 用真 Lua 虚拟机（fengari）验证 v4.0.2 的纯逻辑改动：
//   ② CutGate 门槛统一、④ 撤销分流（lastUndoKind）、⑥ ctlTextOrSnap helper
// 做法与 test_diag_text.js 一致：摘掉末尾 App.OnRun() 启动，return App，
// 再 mock 掉会连 Resolve / 刷新界面的副作用方法，只测我们要的逻辑。
const fs = require("fs");
const path = require("path");
const { lua, lauxlib, lualib, to_luastring } = require("fengari");

const file = process.argv[2] || "AsrSubs.lua";
let code = fs.readFileSync(path.resolve(file), "utf8");
// 摘掉入口三行
code = code.replace(/if resolve == nil and type\(Resolve\) == "function" then[\s\S]*?App\.OnRun\(\)\s*$/, "");
code += "\nreturn App\n";

const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);

// 覆盖 io.open 为永远打不开（fengari 没有真文件系统）
lua.lua_getglobal(L, to_luastring("_G"));
lua.lua_pushliteral(L, "io");
lua.lua_createtable(L, 0, 1);
lua.lua_pushliteral(L, "open");
lua.lua_pushcfunction(L, function (L) { lua.lua_pushnil(L); return 1; });
lua.lua_settable(L, -3);
lua.lua_settable(L, -3);
lua.lua_pop(L, 1);

if (lauxlib.luaL_loadbuffer(L, to_luastring(code), code.length,
    to_luastring("@AsrSubs.lua")) !== lua.LUA_OK) {
  console.error("编译失败：" + lua.lua_tojsstring(L, -1)); process.exit(1);
}
if (lua.lua_pcall(L, 0, 1, 0) !== lua.LUA_OK) {
  console.error("加载期执行失败：" + lua.lua_tojsstring(L, -1)); process.exit(1);
}
// 栈顶 = App
const AppIdx = lua.lua_gettop(L);

// 取 App.S（App 表里 S 字段）
function getS() { lua.lua_getfield(L, AppIdx, to_luastring("S")); return lua.lua_gettop(L); }

// 调 App 的方法：name(...args)  —— 用冒号语义，self=App
// ★ CutGate 返回 2 个值 (ok, reason)，需要 nresults=2 才能都拿到。
//   返回 [v1, v2]：v1=ok(true/false)，v2=reason 字符串。
function callMethod2(name, setupArgs) {
  lua.lua_getfield(L, AppIdx, to_luastring(name));
  lua.lua_pushvalue(L, AppIdx);           // self
  const n = setupArgs ? setupArgs(L) : 0; // 额外参数个数
  if (lua.lua_pcall(L, 1 + n, 2, 0) !== lua.LUA_OK) {
    throw new Error(name + " 抛错: " + lua.lua_tojsstring(L, -1));
  }
  const v2 = lua.lua_tojsstring(L, -1);   // 栈顶：第二个返回值 reason
  const v1 = lua.lua_toboolean(L, -2);    // 其下：第一个返回值 ok
  lua.lua_pop(L, 2);
  return [v1, v2];
}
function callMethod(name, setupArgs) {
  lua.lua_getfield(L, AppIdx, to_luastring(name));
  lua.lua_pushvalue(L, AppIdx);           // self
  const n = setupArgs ? setupArgs(L) : 0; // 额外参数个数
  if (lua.lua_pcall(L, 1 + n, 2, 0) !== lua.LUA_OK) {
    throw new Error(name + " 抛错: " + lua.lua_tojsstring(L, -1));
  }
  const reason = lua.lua_tojsstring(L, -1);
  lua.lua_pop(L, 2);
  return reason;                          // 返回栈顶的 reason 字符串
}

// 往 App.S 里写一个字段
function setS(field, pushVal) {
  const s = getS();
  lua.lua_pushliteral(L, field);
  pushVal(L);
  lua.lua_settable(L, s);
  lua.lua_pop(L, 1); // pop S
}
function pushStr(v){ return function(L){ lua.lua_pushliteral(L, v); }; }
function pushBool(v){ return function(L){ lua.lua_pushboolean(L, v?1:0); }; }
function pushNum(v){ return function(L){ lua.lua_pushnumber(L, v); }; }

// CutStats 依赖 App.S.cuts / cutSrt / srt；我们直接构造表
function setCuts(arr) { // arr: [[s,e],...]
  const s = getS();
  lua.lua_pushliteral(L, "cuts");
  lua.lua_createtable(L, arr.length, 0);
  arr.forEach(function(p, i){
    lua.lua_pushnumber(L, i+1);
    lua.lua_createtable(L, 0, 2);
    lua.lua_pushliteral(L,"s"); lua.lua_pushnumber(L,p[0]); lua.lua_settable(L,-3);
    lua.lua_pushliteral(L,"e"); lua.lua_pushnumber(L,p[1]); lua.lua_settable(L,-3);
    lua.lua_settable(L,-3);
  });
  lua.lua_settable(L, s);
  lua.lua_pop(L, 1);
}
function setSubs(n) {
  const s = getS();
  lua.lua_pushliteral(L, "subs");
  lua.lua_createtable(L, n, 0);
  for (let i=1;i<=n;i++){ lua.lua_pushnumber(L,i); lua.lua_createtable(L,0,1); lua.lua_settable(L,-3); }
  lua.lua_settable(L, s);
  lua.lua_pop(L, 1);
}
// 读 App.S 的字符串字段
function getSStr(field){
  lua.lua_getfield(L, AppIdx, to_luastring("S"));
  lua.lua_getfield(L, -1, to_luastring(field));
  const v = lua.lua_tojsstring(L, -1);
  lua.lua_pop(L, 2);
  return v;
}
// 判断 App.S.cuts[i] 是否为 nil（用于验证撤销后区间被回滚）
function isCutNilAt(i){
  lua.lua_getfield(L, AppIdx, to_luastring("S"));
  lua.lua_getfield(L, -1, to_luastring("cuts"));
  lua.lua_rawgeti(L, -1, i);
  const isNil = lua.lua_isnil(L, -1);
  lua.lua_pop(L, 3);
  return isNil;
}

// mock 副作用方法：UndoDelete 会调 Status/Log，我们让它们返回空
["Status","Log","RefreshCutBar","RefreshDirty","FillSubTree"].forEach(function(m){
  lua.lua_pushcfunction(L, function(L){ lua.lua_pushliteral(L,""); return 1; });
  lua.lua_setfield(L, AppIdx, to_luastring(m));
});

let pass=0, fail=0;
function check(name, got, want){
  const ok = (got===want);
  console.log((ok?"  OK  ":" FAIL ")+name+"  got="+JSON.stringify(got)+" want="+JSON.stringify(want));
  ok?pass++:fail++;
}

// ================= ② CutGate 门槛测试 =================
// 场景1：一切就绪，有删除记录且已写回 → ok=true, reason=""
setS("phase", pushStr("done"));
setSubs(5);
setS("srt", pushStr("C:/x/a.srt"));
setS("cutSrt", pushStr("C:/x/a.srt"));
setCuts([[1,3],[5,8]]);
setS("subsDirty", pushBool(false));
setS("pendingSync", function(L){lua.lua_pushnil(L);});
const [ok1, why1] = callMethod2("CutGate", function(L){return 0;});
check("② 就绪可剪 ok=true", ok1, true);
check("② 就绪可剪 reason 空", why1, "");

// 场景2：字幕未写回（dirty）→ 被拦，原因含「字幕更新」
setS("subsDirty", pushBool(true));
const [ok2, why2] = callMethod2("CutGate", function(L){return 0;});
check("② 未写回被拦 ok=false", ok2, false);
check("② 未写回被拦 reason", why2, "先点「字幕更新」写回，再点「视频粗剪」");

// 场景3：换字幕导致作废（cutSrt != srt 且有 cuts）→ 专门提示
setS("subsDirty", pushBool(false));
setS("cutSrt", pushStr("C:/x/OLD.srt"));
const [ok3, why3] = callMethod2("CutGate", function(L){return 0;});
check("② 换字幕作废 ok=false", ok3, false);
check("② 换字幕作废 reason", why3, "换了字幕文件，旧的删除记录已作废，请重新删除要剪的行");

// 场景4：pendingSync → 拦
setS("cutSrt", pushStr("C:/x/a.srt"));
setS("pendingSync", pushNum(9999999999));
const [ok4, why4] = callMethod2("CutGate", function(L){return 0;});
check("② 正在写回 ok=false", ok4, false);
check("② 正在写回 reason", why4, "字幕正在写回，等一两秒再点「视频粗剪」");
setS("pendingSync", function(L){lua.lua_pushnil(L);});

// ================= ④ 撤销分流测试 =================
// 关键回归：粗剪(lastUndoKind=cut) 之后再删字幕(SnapshotUndo 标 subs)，撤销应能撤字幕
setS("lastUndoKind", pushStr("cut"));
setS("lastCut", pushStr("C:/x/cut_pre.json"));
setS("subsUndo", function(L){lua.lua_pushnil(L);});
// 模拟用户又删了一行 → SnapshotUndo
setSubs(5);
callMethod("SnapshotUndo", function(L){return 0;});
// 读 lastUndoKind 是否被改成 subs
lua.lua_getfield(L, AppIdx, to_luastring("S"));
lua.lua_getfield(L, -1, to_luastring("lastUndoKind"));
const kind = lua.lua_tojsstring(L, -1);
lua.lua_pop(L, 2);
check("④ 删字幕后 kind=subs", kind, "subs");
// 现在撤销：应走字幕快照回滚，而不是被 cut 挡住。撤销后 subsUndo 被清空
callMethod("UndoDelete", function(L){return 0;});
lua.lua_getfield(L, AppIdx, to_luastring("S"));
lua.lua_getfield(L, -1, to_luastring("subsUndo"));
const isNil = lua.lua_isnil(L, -1);
lua.lua_pop(L, 2);
check("④ 撤销真的回滚了字幕快照", isNil, true); // subsUndo 被消费→nil 说明走了字幕分支

// ================= ⑤ v4.0.4 bug A：撤销删行同步回滚 cuts =================
// 场景：删行前 SnapshotUndo 拍下 cuts(1段)，删行后 RecordCuts 把区间并进去(变2段)，
//   再撤销删行 —— cuts 必须回到 1 段，否则字幕已恢复、cut 却还在 → 再粗剪会误切。
setS("lastUndoKind", pushStr("subs"));
setS("subsDirty", pushBool(false));
setS("pendingSync", function(L){lua.lua_pushnil(L);});
setSubs(5);
setCuts([[1,3]]);              // 删行前：只有 1 段
setS("cutSrt", pushStr("C:/x/a.srt"));
callMethod("SnapshotUndo", function(L){return 0;});   // 拍快照
setCuts([[1,3],[5,8]]);        // 模拟删行后 RecordCuts 累加成 2 段
setS("cutSrt", pushStr("C:/x/B.srt"));  // 顺带改 cutSrt，撤销也应还原
callMethod("UndoDelete", function(L){return 0;});     // 撤销删行
check("⑤ 撤销删行后 cuts 第2段被回滚", isCutNilAt(2), true);
check("⑤ 撤销删行后 cutSrt 还原", getSStr("cutSrt"), "C:/x/a.srt");

console.log("\n"+(fail===0?"=== 逻辑测试全部通过 ("+pass+") ===":"!!! "+fail+" 项失败 !!!"));
process.exit(fail===0?0:1);
