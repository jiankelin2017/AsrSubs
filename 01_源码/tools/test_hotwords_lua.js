// 用真 Lua 虚拟机读一遍 hotwords.lua，确认 Lua 侧能正确消费这份数据。
//
// 为什么要单独测：Python 写出的是 UTF-8 字节流，Lua 的字符串是【字节串】。
// 中文词原样过一遍没问题，但引号/反斜杠/制表符这类转义一旦写错，
// loadfile 会在运行期才报错 —— 而那时的现象是"底部那行永远是空的"，
// 排查起来极难。这里提前把它验掉。
//
// 用法：
//   NODE_PATH=<node_modules> node tools/test_hotwords_lua.js <hotwords.lua>
const fs = require("fs");
const path = require("path");
const { lua, lauxlib, lualib, to_luastring } = require("fengari");

const file = process.argv[2];
if (!file || !fs.existsSync(file)) {
  console.error("用法：node tools/test_hotwords_lua.js <hotwords.lua>");
  process.exit(2);
}

const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);

const code = fs.readFileSync(file, "utf8");
const st = lauxlib.luaL_loadbuffer(
  L,
  to_luastring(code),
  code.length,
  to_luastring("@" + path.basename(file))
);
if (st !== lua.LUA_OK) {
  console.error("编译失败：" + lua.lua_tojsstring(L, -1));
  process.exit(1);
}
if (lua.lua_pcall(L, 0, 1, 0) !== lua.LUA_OK) {
  console.error("执行失败：" + lua.lua_tojsstring(L, -1));
  process.exit(1);
}

// 模拟 App:RefreshHotwords 的取值方式：t.total / t.thr / t.words[i][1..2]
lua.lua_getfield(L, -1, to_luastring("total"));
const total = lua.lua_tointeger(L, -1);
lua.lua_pop(L, 1);
lua.lua_getfield(L, -1, to_luastring("thr"));
const thr = lua.lua_tointeger(L, -1);
lua.lua_pop(L, 1);
lua.lua_getfield(L, -1, to_luastring("words"));
const n = lua.lua_rawlen(L, -1);

const parts = [];
for (let i = 1; i <= n; i++) {
  lua.lua_rawgeti(L, -1, i);
  lua.lua_rawgeti(L, -1, 1);
  const w = lua.lua_tojsstring(L, -1);
  lua.lua_pop(L, 1);
  lua.lua_rawgeti(L, -1, 2);
  const c = lua.lua_tointeger(L, -1);
  lua.lua_pop(L, 1);
  lua.lua_pop(L, 1);
  parts.push(w + " " + c);
}
lua.lua_pop(L, 1);

console.log("total=" + total + " thr=" + thr + " n=" + n);
console.log("界面那一行会显示：");
console.log("  " + parts.join("  ·  "));

// 校验：次数必须单调不增（用户要求「从多到少排序」）
let prev = Infinity;
let ok = true;
for (const p of parts) {
  const c = parseInt(p.split(" ").pop(), 10);
  if (c > prev) ok = false;
  prev = c;
}
console.log(ok ? "排序检查通过（次数单调不增）" : "排序检查失败！");
process.exit(ok ? 0 : 1);
