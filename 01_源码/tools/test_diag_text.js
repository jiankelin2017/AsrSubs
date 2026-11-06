// 用真 Lua 虚拟机跑一遍 App:DiagText()，确认诊断文本能拼出来、不会抛错。
//
// 为什么值得单独测：这段是 v2.0.9 新增的诊断窗口内容。它一旦抛错，
// 用户点版本号只会看到「诊断窗口打不开」—— 而这正是最需要诊断的时候。
//
// 做法：把构建产物尾部那句 `App.OnRun()` 摘掉再 load（否则会去连 Resolve），
// 再把 io 换成空实现（fengari 没有真文件系统）。于是所有"读文件"的分支
// 都走降级路径，正好把「env.lua / last_run.lua 不存在」这类边界也覆盖掉。
//
// 用法：
//   NODE_PATH=<node_modules> node tools/test_diag_text.js "AsrSubs.lua"
const fs = require("fs");
const path = require("path");
const { lua, lauxlib, lualib, to_luastring } = require("fengari");

const file = process.argv[2] || "AsrSubs.lua";
let code = fs.readFileSync(path.resolve(file), "utf8");

// 摘掉最后的启动调用，改成 return App
//   ★ App 在 02_env.lua 里是 `local App = {}`，外部拿不到。
//     只能在 chunk 末尾追加 return —— 那时它还在作用域里。
code = code.replace(/App\.OnRun\(\)\s*$/, "");
code += "\nreturn App\n";

const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);

// 用空 io 覆盖：fengari 的 io 没有 open，读文件的分支会直接报错。
// 这里给一个"永远打不开"的实现，正好模拟"文件不存在"。
lua.lua_getglobal(L, to_luastring("_G"));
lua.lua_pushliteral(L, "io");
lua.lua_createtable(L, 0, 1);
lua.lua_pushliteral(L, "open");
lua.lua_pushcfunction(L, function (L) {
  lua.lua_pushnil(L);
  return 1;
});
lua.lua_settable(L, -3);
lua.lua_settable(L, -3);
lua.lua_pop(L, 1);

if (lauxlib.luaL_loadbuffer(L, to_luastring(code), code.length,
    to_luastring("@AsrSubs.lua")) !== lua.LUA_OK) {
  console.error("编译失败：" + lua.lua_tojsstring(L, -1));
  process.exit(1);
}
if (lua.lua_pcall(L, 0, 1, 0) !== lua.LUA_OK) {
  console.error("加载期执行失败：" + lua.lua_tojsstring(L, -1));
  process.exit(1);
}

// 栈顶现在是 App。App:DiagText()
lua.lua_getfield(L, -1, to_luastring("DiagText"));
lua.lua_pushvalue(L, -2);
if (lua.lua_pcall(L, 1, 1, 0) !== lua.LUA_OK) {
  console.error("App:DiagText() 抛错：" + lua.lua_tojsstring(L, -1));
  process.exit(1);
}

const text = lua.lua_tojsstring(L, -1);
console.log(text);
console.log("");
console.log("—— 长度 " + text.length + " 字符，DiagText 未抛错 ——");
process.exit(0);
