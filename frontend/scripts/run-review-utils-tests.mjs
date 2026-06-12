import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const root = process.cwd();
const outDir = path.join(root, ".test-dist");
const utilSource = path.join(root, "src", "reviewUtils.ts");
const utilOutput = path.join(outDir, "reviewUtils.js");
const testFile = path.join(root, "tests", "review-utils.test.mjs");

const ts = await import(pathToFileURL(path.join(root, "node_modules", "typescript", "lib", "typescript.js")).href);

await rm(outDir, { recursive: true, force: true });
await mkdir(outDir, { recursive: true });

const sourceText = await readFile(utilSource, "utf8");
const transpiled = ts.transpileModule(sourceText, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2020
  },
  fileName: "reviewUtils.ts"
});

await writeFile(utilOutput, transpiled.outputText, "utf8");

try {
  await import(pathToFileURL(testFile).href);
} finally {
  await rm(outDir, { recursive: true, force: true });
}
