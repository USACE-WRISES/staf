import { promises as fs } from 'fs';
import path from 'path';
import { buildToolbox, toolboxPath, workbookSnapshot } from '../src/lib/metricLibrary/toolbox.ts';

const exists = async (filePath: string) => {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
};

const main = async () => {
  const { buffer, metricCount, curveRowCount } = await buildToolbox();
  const counts = `${metricCount} metrics, ${curveRowCount} reference curve rows`;
  // Rewrite only on a real change, so a rebuild of an unchanged library leaves git clean.
  if (
    (await exists(toolboxPath)) &&
    (await workbookSnapshot(toolboxPath)) === (await workbookSnapshot(buffer))
  ) {
    // eslint-disable-next-line no-console
    console.log(`${path.basename(toolboxPath)} is up to date (${counts}).`);
    return;
  }
  await fs.writeFile(toolboxPath, buffer);
  // eslint-disable-next-line no-console
  console.log(`Wrote ${toolboxPath} (${counts}).`);
};

main().catch((error) => {
  // eslint-disable-next-line no-console
  console.error(error);
  process.exit(1);
});
