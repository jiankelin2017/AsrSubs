// Lua 语法静态校验（构建产物 + 每个分片）
// 用法: node tools/check_lua.js "AsrSubs.lua" lua_src/05_ui.lua ...
const fs = require("fs");
const path = require("path");
const luaparse = require("luaparse");

const files = process.argv.slice(2);
let bad = 0;

for (const f of files) {
  const code = fs.readFileSync(f, "utf8");
  try {
    luaparse.parse(code, { luaVersion: "5.3", comments: false, scope: false });
    console.log("OK   " + f);
  } catch (e) {
    bad++;
    const line = e.line || (e.index != null ? code.slice(0, e.index).split("\n").length : "?");
    console.log("FAIL " + f + "  行 " + line + "：" + e.message);
    if (e.index != null) {
      const lines = code.split("\n");
      const l = Number(line);
      for (let i = Math.max(0, l - 3); i < Math.min(lines.length, l + 2); i++) {
        console.log("      " + (i + 1) + " | " + lines[i]);
      }
    }
  }
}
console.log(bad === 0 ? "全部通过" : bad + " 个文件有语法错误");
process.exit(bad === 0 ? 0 : 1);
