import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const actorName = (process.env.ACTOR_NAME || '').trim();
const inputPath = process.env.ACTOR_INPUT_PATH || path.resolve('run-output/input.json');

if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(actorName)) {
  throw new Error(`Invalid Actor name: ${actorName}`);
}

const actorEntrypoint = path.resolve('actors', actorName, 'index.js');
const actorDir = path.dirname(actorEntrypoint);

try {
  await fs.access(actorEntrypoint);
} catch {
  throw new Error(`Actor not found: ${actorName}. Expected ${actorEntrypoint}`);
}

const rawInput = await fs.readFile(inputPath, 'utf8');
const input = JSON.parse(rawInput || '{}');

await fs.mkdir('run-output', { recursive: true });

const actorModule = await import(pathToFileURL(actorEntrypoint).href);
if (typeof actorModule.run !== 'function') {
  throw new Error(`Actor ${actorName} must export async function run(input, context)`);
}

const context = {
  actorName,
  actorDir,
  outputDir: path.resolve('run-output'),
};

const result = await actorModule.run(input, context);

await fs.writeFile(
  path.join(context.outputDir, 'result.json'),
  JSON.stringify(result ?? null, null, 2),
  'utf8',
);

console.log(`Actor ${actorName} completed.`);
