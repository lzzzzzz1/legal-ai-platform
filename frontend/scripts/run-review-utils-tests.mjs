import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const root = process.cwd();
const outDir = path.join(root, ".test-dist");
const utilSource = path.join(root, "src", "reviewUtils.ts");
const utilOutput = path.join(outDir, "reviewUtils.js");
const jobsSource = path.join(root, "src", "api", "reviewJobs.ts");
const jobsOutput = path.join(outDir, "reviewJobs.js");
const clientSource = path.join(root, "src", "api", "client.ts");
const clientOutput = path.join(outDir, "client.js");
const testFile = path.join(root, "tests", "review-utils.test.mjs");
const jobsTestFile = path.join(root, "tests", "review-job-utils.test.mjs");
const assistantMarkTestFile = path.join(root, "tests", "legal-assistant-mark.test.mjs");

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

const jobsSourceText = await readFile(jobsSource, "utf8");
const jobsTranspiled = ts.transpileModule(jobsSourceText, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2020
  },
  fileName: "reviewJobs.ts"
});
// Node's ESM test runner requires an explicit extension, whereas Vite resolves
// the source import during normal application builds.
await writeFile(
  jobsOutput,
  jobsTranspiled.outputText.replaceAll('from "./client"', 'from "./client.js"'),
  "utf8"
);

// Keep the lightweight Node test harness aligned with API modules extracted
// from reviewJobs.  TypeScript preserves the relative import as "./client".
const clientSourceText = await readFile(clientSource, "utf8");
const clientTranspiled = ts.transpileModule(clientSourceText, {
  compilerOptions: {
    module: ts.ModuleKind.ES2022,
    target: ts.ScriptTarget.ES2020
  },
  fileName: "client.ts"
});
await writeFile(clientOutput, clientTranspiled.outputText, "utf8");

try {
  await import(pathToFileURL(testFile).href);
  await import(pathToFileURL(jobsTestFile).href);
  await import(pathToFileURL(assistantMarkTestFile).href);
} finally {
  await rm(outDir, { recursive: true, force: true });
}
