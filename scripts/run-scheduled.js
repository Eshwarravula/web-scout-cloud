import fs from 'node:fs/promises';
import path from 'node:path';
import { CronExpressionParser } from 'cron-parser';
import { spawn } from 'node:child_process';

const config = JSON.parse(await fs.readFile('config/schedules.json', 'utf8'));
const schedules = Array.isArray(config.schedules) ? config.schedules : [];
const now = new Date();
const lookbackMs = 70 * 60 * 1000;

await fs.mkdir('run-output', { recursive: true });

let ran = 0;

for (const schedule of schedules) {
  if (!schedule.enabled) continue;

  const expression = CronExpressionParser.parse(schedule.cron, {
    currentDate: now,
    tz: schedule.timezone || 'UTC',
  });
  const previous = expression.prev().toDate();
  const age = now.getTime() - previous.getTime();
  if (age < 0 || age > lookbackMs) continue;

  const safe = String(schedule.name || schedule.actor).replace(/[^a-zA-Z0-9._-]+/g, '-');
  const dir = path.join('run-output', safe);
  await fs.mkdir(dir, { recursive: true });
  const inputPath = path.join(dir, 'input.json');
  await fs.writeFile(inputPath, JSON.stringify(schedule.input ?? {}, null, 2));

  console.log(`Running schedule ${schedule.name} -> ${schedule.actor}`);

  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['scripts/run-actor.js'], {
      stdio: 'inherit',
      env: {
        ...process.env,
        ACTOR_NAME: schedule.actor,
        ACTOR_INPUT_PATH: path.resolve(inputPath),
      },
    });
    child.on('exit', (code) => code === 0 ? resolve() : reject(new Error(`Actor exited ${code}`)));
    child.on('error', reject);
  });

  for (const filename of ['result.json', 'dataset.json', 'summary.md']) {
    try {
      await fs.rename(path.join('run-output', filename), path.join(dir, filename));
    } catch {}
  }

  ran += 1;
}

console.log(ran ? `Ran ${ran} scheduled Actor(s).` : 'No scheduled Actors are due.');
