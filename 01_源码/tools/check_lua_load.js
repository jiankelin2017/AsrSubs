// 用真正的 Lua 5.3 虚拟机（fengari）编译一遍构建产物。
//
// 为什么不能只靠 luaparse（check_lua.js）：
//   luaparse 只做语法分析，**查不出 "too many local variables"**。
//   Lua 每个函数最多 200 个 local，超了是【加载期】直接失败，
//   而且报错信息和语法无关，特别容易被当成"文件坏了"。
//   本工程的 BuildUI 就是个巨型函数，每次往里加控件都在逼近这条线。
//
// 用法：
//   NODE_PATH=<node_modules> node tools/check_lua_load.js "AsrSubs.lua"
const fs = require("fs");
const path = require("path");
const { lua, lauxlib, lualib, to_luastring } = require("fengari");

const file = process.argv[2] || "AsrSubs.lua";
const abs = path.resolve(file);

if (!fs.existsSync(abs)) {
  console.error("找不到文件：" + abs);
  process.exit(2);
}

const code = fs.readFileSync(abs, "utf8");
const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);

// 只编译，不执行 —— 目的就是让虚拟机把常量表/寄存器分配走一遍
const status = lauxlib.luaL_loadbuffer(
  L,
  to_luastring(code),
  code.length,
  to_luastring("@" + path.basename(abs))
);

if (status !== lua.LUA_OK) {
  const msg = lua.lua_tojsstring(L, -1);
  console.error("编译失败：" + msg);
  process.exit(1);
}

// 顺带数一下【顶格】的 local（= 最外层 chunk 的局部变量），接近 200 时提前告警。
// 注意：函数内部的 local 属于那个函数，不占这里的名额，所以只数行首不带空格的。
const nLocal = (code.match(/^local\s+\w+/gm) || []).length;
console.log(
  "OK  " + path.basename(abs) + "（编译通过，最外层 local " + nLocal + " 个）"
);
