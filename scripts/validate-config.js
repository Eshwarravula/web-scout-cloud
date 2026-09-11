import fs from 'node:fs/promises';
import path from 'node:path';
import { CronExpressionParser } from 'cron-parser';

const config = JSON.parse(await fs.readFile('config/schedules.json', 'utf8'));
if (!Array.isArray(config.schedules)) throw new Error('config/schedules.json must contain a schedules array');

for (const [index, schedule] of config.schedules.entries()) {
  if (!schedule.name || !schedule.actor || !schedule.cron) {
    throw new Error(`Schedule ${index} requires name, actor and cron`);
  }
  await fs.access(path.resolve('actors', schedule.actor, 'index.js'));
  CronExpressionParser.parse(schedule.cron, {
    currentDate: new Date(),
    tz: schedule.timezone || 'UTC',
  });
}

console.log('Configuration is valid.');
