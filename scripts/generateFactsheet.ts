import { promises as fs } from 'fs';
import path from 'path';
import {
  Document,
  Packer,
  Paragraph,
  TextRun,
  Table,
  TableRow,
  TableCell,
  WidthType,
  AlignmentType,
  ShadingType,
  BorderStyle,
  VerticalAlign,
  TableLayoutType,
  ExternalHyperlink,
  convertInchesToTwip,
} from 'docx';

/* ────────────────────────────────────────────
   Color & style constants
   ──────────────────────────────────────────── */

const BLUE_DARK = '2b5a7c';
const BLUE_LIGHT_BG = 'f5f8fa';
const BODY_COLOR = '333333';
const WHITE = 'FFFFFF';

const SCREENING_BG = 'f0f4f7';
const RAPID_BG = 'e6e6e6';
const DETAILED_BG = 'd0d0d0';

const PHYSICAL_BORDER = '4a90a4';
const CHEMICAL_BORDER = '6b8e23';
const BIOLOGICAL_BORDER = 'b8860b';
const ECOSYSTEM_BORDER = '2b5a7c';

const FONT = 'Calibri';
const BODY_SIZE = 20;       // half-points → 10pt
const SMALL_SIZE = 18;      // 9pt
const HEADING_SIZE = 24;    // 12pt
const TITLE_SIZE = 32;      // 16pt
const SUBTITLE_SIZE = 22;   // 11pt
const CARD_TITLE_SIZE = 21; // 10.5pt

const NONE_BORDER = { style: BorderStyle.NONE, size: 0, color: WHITE };
const NO_BORDERS = {
  top: NONE_BORDER,
  bottom: NONE_BORDER,
  left: NONE_BORDER,
  right: NONE_BORDER,
};

/* ────────────────────────────────────────────
   Helpers
   ──────────────────────────────────────────── */

function sectionHeading(text: string): Paragraph {
  return new Paragraph({
    spacing: { before: 200, after: 80 },
    border: {
      bottom: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK, space: 2 },
    },
    children: [
      new TextRun({
        text,
        bold: true,
        size: HEADING_SIZE,
        color: BLUE_DARK,
        font: FONT,
      }),
    ],
  });
}

function bodyParagraph(text: string, spacing?: { before?: number; after?: number }): Paragraph {
  return new Paragraph({
    spacing: { before: spacing?.before ?? 60, after: spacing?.after ?? 60 },
    children: [
      new TextRun({ text, size: BODY_SIZE, color: BODY_COLOR, font: FONT }),
    ],
  });
}

function bodyRuns(runs: TextRun[], spacing?: { before?: number; after?: number }): Paragraph {
  return new Paragraph({
    spacing: { before: spacing?.before ?? 60, after: spacing?.after ?? 60 },
    children: runs,
  });
}

function boldBodyRun(text: string): TextRun {
  return new TextRun({ text, bold: true, size: BODY_SIZE, color: BODY_COLOR, font: FONT });
}

function normalBodyRun(text: string): TextRun {
  return new TextRun({ text, size: BODY_SIZE, color: BODY_COLOR, font: FONT });
}

function smallRun(text: string, opts?: { bold?: boolean; color?: string; italics?: boolean }): TextRun {
  return new TextRun({
    text,
    size: SMALL_SIZE,
    color: opts?.color ?? BODY_COLOR,
    font: FONT,
    bold: opts?.bold,
    italics: opts?.italics,
  });
}

function bulletItem(runs: TextRun[]): Paragraph {
  return new Paragraph({
    spacing: { before: 40, after: 40 },
    bullet: { level: 0 },
    children: runs,
  });
}

function smallBulletItem(runs: TextRun[]): Paragraph {
  return new Paragraph({
    spacing: { before: 30, after: 30 },
    bullet: { level: 0 },
    children: runs,
  });
}

/* ────────────────────────────────────────────
   Header table (dark blue bar)
   ──────────────────────────────────────────── */

function headerTable(subtitle: string): Table {
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    rows: [
      new TableRow({
        children: [
          new TableCell({
            shading: { type: ShadingType.CLEAR, fill: BLUE_DARK, color: WHITE },
            borders: NO_BORDERS,
            width: { size: 100, type: WidthType.PERCENTAGE },
            children: [
              new Paragraph({
                alignment: AlignmentType.CENTER,
                spacing: { before: 120, after: 0 },
                children: [
                  new TextRun({
                    text: 'Stream Tiered Assessment Framework (STAF)',
                    bold: true,
                    size: TITLE_SIZE,
                    color: WHITE,
                    font: FONT,
                  }),
                ],
              }),
              new Paragraph({
                alignment: AlignmentType.CENTER,
                spacing: { before: 40, after: 120 },
                children: [
                  new TextRun({
                    text: subtitle,
                    size: SUBTITLE_SIZE,
                    color: WHITE,
                    font: FONT,
                    italics: true,
                  }),
                ],
              }),
            ],
          }),
        ],
      }),
    ],
  });
}

/* ────────────────────────────────────────────
   Tier card cell
   ──────────────────────────────────────────── */

function tierCardCell(
  title: string,
  fill: string,
  goal: string,
  dataSources: string,
  effort: string,
  uses: string,
): TableCell {
  return new TableCell({
    shading: { type: ShadingType.CLEAR, fill, color: '000000' },
    borders: NO_BORDERS,
    margins: { top: 60, bottom: 60, left: 80, right: 80 },
    width: { size: 33, type: WidthType.PERCENTAGE },
    children: [
      new Paragraph({
        spacing: { after: 60 },
        children: [
          new TextRun({ text: title, bold: true, size: CARD_TITLE_SIZE, color: BLUE_DARK, font: FONT }),
        ],
      }),
      bodyRuns([boldBodyRun('Goal: '), normalBodyRun(goal)], { before: 30, after: 30 }),
      bodyRuns([boldBodyRun('Data sources: '), normalBodyRun(dataSources)], { before: 30, after: 30 }),
      bodyRuns([boldBodyRun('Effort: '), normalBodyRun(effort)], { before: 30, after: 30 }),
      bodyRuns([boldBodyRun('Uses: '), normalBodyRun(uses)], { before: 30, after: 30 }),
    ],
  });
}

/* ────────────────────────────────────────────
   Outcome card cell (colored top border)
   ──────────────────────────────────────────── */

function outcomeCardCell(
  title: string,
  topColor: string,
  definition: string,
  examples: string,
): TableCell {
  return new TableCell({
    shading: { type: ShadingType.CLEAR, fill: 'fafafa', color: '000000' },
    borders: {
      top: { style: BorderStyle.SINGLE, size: 6, color: topColor },
      bottom: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
      left: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
      right: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
    },
    margins: { top: 50, bottom: 50, left: 60, right: 60 },
    width: { size: 3400, type: WidthType.DXA },
    children: [
      new Paragraph({
        spacing: { after: 40 },
        children: [
          new TextRun({ text: title, bold: true, size: SMALL_SIZE, color: topColor, font: FONT }),
        ],
      }),
      new Paragraph({
        spacing: { before: 20, after: 30 },
        children: [smallRun(definition)],
      }),
      new Paragraph({
        spacing: { before: 20, after: 20 },
        children: [smallRun('Examples: ', { bold: true }), smallRun(examples)],
      }),
    ],
  });
}

/* ────────────────────────────────────────────
   PAGE 1
   ──────────────────────────────────────────── */

function buildPage1(): (Table | Paragraph)[] {
  const elements: (Table | Paragraph)[] = [];

  // Header
  elements.push(headerTable('A structured approach to assessing stream ecosystem condition'));

  // Background and Need
  elements.push(sectionHeading('Background and Need'));
  elements.push(bodyParagraph(
    'USACE planners, engineers, and practitioners need tools for holistically assessing stream structure, function, and dynamic processes. Existing approaches have often relied on species-specific habitat models that focus narrowly on single taxa and do not capture ecosystem-scale outcomes. At the same time, the Civil Works planning process (SMART Planning) imposes different modeling needs as projects progress, from rapid site screening with sparse data (1\u20133 months) through conceptual design (12\u201320 months) to final design and post-construction monitoring. There is no standardized, nationally applicable framework that scales assessment effort to project phase while keeping results comparable.',
  ));
  elements.push(bodyParagraph(
    'A review of 188 stream assessment methods in the U.S. found that most methods do not evaluate the full range of ecosystem functions, there is limited guidance on matching effort to project phase, and results are difficult to compare across methods, projects, and regions.',
  ));

  // The Framework
  elements.push(sectionHeading('The Framework'));

  // Tier cards table
  elements.push(
    new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      columnWidths: [3552, 3552, 3552],
      layout: TableLayoutType.FIXED,
      rows: [
        new TableRow({
          children: [
            tierCardCell(
              'Screening Tier',
              SCREENING_BG,
              'Provides a broad, watershed or landscape-scale snapshot. Identifies constraints, opportunities, and where additional effort may be needed.',
              'Desktop GIS, existing data review, limited site photos, and brief recon where available.',
              'Minutes to hours.',
              'Scoping and site screening.',
            ),
            tierCardCell(
              'Rapid Tier',
              RAPID_BG,
              'Refines understanding of site conditions. Field evidence supports planning decisions and alternatives comparison.',
              'Focused field observations, many site photos, limited modeling or lab data, desktop GIS, existing data.',
              'Hours to days.',
              'Design, comparing alternatives.',
            ),
            tierCardCell(
              'Detailed Tier',
              DETAILED_BG,
              'Provides high confidence, site-specific data to support final design decisions and performance evaluation.',
              'Intensive field data collection, extensive modeling or laboratory analysis, site photos, desktop GIS, existing data.',
              'Days to weeks.',
              'Final design, post-construction monitoring, regional studies.',
            ),
          ],
        }),
      ],
    }),
  );

  // Effort arrow caption
  elements.push(
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 80, after: 80 },
      children: [
        new TextRun({ text: 'Lower  \u2190\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015 Effort, data needs, confidence \u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2015\u2192  Higher', size: SMALL_SIZE, color: BODY_COLOR, font: FONT, italics: true }),
      ],
    }),
  );

  // Common foundation callout
  elements.push(
    new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      columnWidths: [10656],
      layout: TableLayoutType.FIXED,
      rows: [
        new TableRow({
          children: [
            new TableCell({
              borders: {
                top: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK },
                bottom: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK },
                left: { style: BorderStyle.SINGLE, size: 4, color: BLUE_DARK },
                right: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK },
              },
              shading: { type: ShadingType.CLEAR, fill: BLUE_LIGHT_BG, color: '000000' },
              margins: { top: 60, bottom: 60, left: 100, right: 100 },
              width: { size: 10656, type: WidthType.DXA },
              children: [
                bodyRuns([
                  boldBodyRun('Common foundation: '),
                  normalBodyRun('Stream functions are consistent across all tiers. The functions are comprehensive and broadly applicable to a wide range of streams.'),
                ]),
              ],
            }),
          ],
        }),
      ],
    }),
  );

  // Comparable results / outcomes box
  elements.push(
    new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      columnWidths: [10656],
      layout: TableLayoutType.FIXED,
      rows: [
        new TableRow({
          children: [
            new TableCell({
              borders: {
                top: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK },
                bottom: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK },
                left: { style: BorderStyle.SINGLE, size: 4, color: BLUE_DARK },
                right: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK },
              },
              shading: { type: ShadingType.CLEAR, fill: BLUE_LIGHT_BG, color: '000000' },
              margins: { top: 60, bottom: 60, left: 100, right: 100 },
              width: { size: 10656, type: WidthType.DXA },
              children: [
                // Heading
                new Paragraph({
                  spacing: { after: 60 },
                  children: [boldBodyRun('Comparable results: Outcomes and Ecosystem Condition')],
                }),
                // Intro
                new Paragraph({
                  spacing: { before: 20, after: 60 },
                  children: [
                    boldBodyRun('Outcomes'),
                    normalBodyRun(' are observable or quantifiable results that are linked to how one or more stream functions operate.'),
                  ],
                }),
                // Outcome cards (nested table)
                new Table({
                  width: { size: 10200, type: WidthType.DXA },
                  columnWidths: [3400, 3400, 3400],
                  layout: TableLayoutType.FIXED,
                  rows: [
                    new TableRow({
                      children: [
                        outcomeCardCell(
                          'Physical Outcomes',
                          PHYSICAL_BORDER,
                          'Physical outcomes are measurable results of hydrologic, hydraulic, geomorphic, and habitat-forming processes. They describe the physical structure of the stream and how water and sediment move through the system.',
                          'floodplain inundation frequency, channel stability, sediment transport balance, habitat unit distribution, substrate composition, and large wood abundance.',
                        ),
                        outcomeCardCell(
                          'Chemical Outcomes',
                          CHEMICAL_BORDER,
                          'Chemical outcomes are measurable results of water chemistry and biogeochemical processes. They describe how chemical conditions support or limit aquatic life and ecosystem processes.',
                          'dissolved oxygen, nutrient concentrations, temperature regime, pH, contaminant levels, and organic matter decomposition rates.',
                        ),
                        outcomeCardCell(
                          'Biological Outcomes',
                          BIOLOGICAL_BORDER,
                          'Biological outcomes are measurable characteristics of aquatic and riparian communities. They describe the presence, abundance, diversity, and functional roles of organisms in the system.',
                          'fish assemblage composition, macroinvertebrate diversity, species richness, presence of sensitive taxa, and functional feeding group distribution.',
                        ),
                      ],
                    }),
                  ],
                }),
                // Ecosystem Condition card (full-width)
                new Table({
                  width: { size: 10200, type: WidthType.DXA },
                  columnWidths: [10200],
                  layout: TableLayoutType.FIXED,
                  rows: [
                    new TableRow({
                      children: [
                        new TableCell({
                          shading: { type: ShadingType.CLEAR, fill: 'fafafa', color: '000000' },
                          borders: {
                            top: { style: BorderStyle.SINGLE, size: 6, color: ECOSYSTEM_BORDER },
                            bottom: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
                            left: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
                            right: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
                          },
                          margins: { top: 50, bottom: 50, left: 60, right: 60 },
                          width: { size: 10200, type: WidthType.DXA },
                          children: [
                            new Paragraph({
                              spacing: { after: 40 },
                              children: [
                                new TextRun({ text: 'Ecosystem Condition', bold: true, size: SMALL_SIZE, color: ECOSYSTEM_BORDER, font: FONT }),
                              ],
                            }),
                            new Paragraph({
                              spacing: { before: 20, after: 20 },
                              children: [
                                smallRun('The overall state of a stream system, based on the combined performance of physical, chemical, and biological outcomes relative to expected or reference conditions. It indicates how well the system sustains ecological function and resilience over time.'),
                              ],
                            }),
                          ],
                        }),
                      ],
                    }),
                  ],
                }),
              ],
            }),
          ],
        }),
      ],
    }),
  );

  return elements;
}

/* ────────────────────────────────────────────
   PAGE 2
   ──────────────────────────────────────────── */

function buildPage2(): (Table | Paragraph)[] {
  const elements: (Table | Paragraph)[] = [];

  // Header
  elements.push(headerTable('Purpose, Functions, Scoring, and Uses'));

  // Spacer between header and Purpose box (prevents Word from merging consecutive tables)
  elements.push(new Paragraph({ spacing: { before: 0, after: 0 } }));

  // Purpose and Objectives box
  elements.push(
    new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      rows: [
        new TableRow({
          children: [
            new TableCell({
              shading: { type: ShadingType.CLEAR, fill: BLUE_LIGHT_BG, color: '000000' },
              borders: {
                top: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
                bottom: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
                left: { style: BorderStyle.SINGLE, size: 6, color: BLUE_DARK },
                right: { style: BorderStyle.SINGLE, size: 1, color: 'cccccc' },
              },
              margins: { top: 80, bottom: 80, left: 120, right: 120 },
              width: { size: 100, type: WidthType.PERCENTAGE },
              children: [
                new Paragraph({
                  spacing: { after: 60 },
                  children: [
                    new TextRun({ text: 'Purpose and Objectives', bold: true, size: HEADING_SIZE, color: BLUE_DARK, font: FONT }),
                  ],
                }),
                bodyParagraph('STAF provides a nested toolkit of stream assessment methods that align with project planning needs at varying levels of effort. The framework provides a common, transferable structure for stream assessment that can be applied at multiple scales and tailored to local needs.'),
                bulletItem([normalBodyRun('Develop a common and transferable structure for stream assessment, applicable at multiple scales and tailored to local needs.')]),
                bulletItem([normalBodyRun('Compile tools for assessing stream outcomes at three scales: screening-level analyses, rapid field assessment, and detailed analyses informed by empirical data collection.')]),
                bulletItem([normalBodyRun('Develop select tools to fill gaps in the existing toolbox.')]),
                bulletItem([normalBodyRun('Demonstrate application of the multi-scale framework as a go-by for users.')]),
              ],
            }),
          ],
        }),
      ],
    }),
  );

  // Spacer between Purpose box and two-column layout
  elements.push(new Paragraph({ spacing: { before: 0, after: 0 } }));

  // Two-column layout
  elements.push(
    new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      rows: [
        new TableRow({
          children: [
            // Left column: Common Stream Functions + Scoring Approach
            new TableCell({
              borders: NO_BORDERS,
              margins: { top: 40, bottom: 40, left: 40, right: 100 },
              width: { size: 50, type: WidthType.PERCENTAGE },
              verticalAlign: VerticalAlign.TOP,
              children: [
                // Common Stream Functions
                new Paragraph({
                  spacing: { before: 100, after: 60 },
                  border: {
                    bottom: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK, space: 2 },
                  },
                  children: [
                    new TextRun({ text: 'Common Stream Functions', bold: true, size: HEADING_SIZE, color: BLUE_DARK, font: FONT }),
                  ],
                }),
                smallBulletItem([smallRun('All tiers assess the same set of stream functions.')]),
                smallBulletItem([smallRun('The tiers differ in how each function is evaluated and documented.')]),
                smallBulletItem([smallRun('This improves consistency across projects and makes it easier to compare results across sites, watersheds, and study phases.')]),

                // Scoring Approach
                new Paragraph({
                  spacing: { before: 160, after: 60 },
                  border: {
                    bottom: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK, space: 2 },
                  },
                  children: [
                    new TextRun({ text: 'Scoring Approach', bold: true, size: HEADING_SIZE, color: BLUE_DARK, font: FONT }),
                  ],
                }),
                smallBulletItem([smallRun('Each stream function is evaluated using tier-appropriate evidence and scored on a 0\u201315 scale.')]),
                new Paragraph({
                  spacing: { before: 30, after: 30 },
                  bullet: { level: 0 },
                  children: [
                    smallRun('Function scores are grouped into three outcome categories: '),
                    smallRun('physical', { bold: true }),
                    smallRun(' (hydrology, hydraulics, geomorphology), '),
                    smallRun('chemical', { bold: true }),
                    smallRun(' (water chemistry, biogeochemical processes), and '),
                    smallRun('biological', { bold: true }),
                    smallRun(' (aquatic and riparian communities).'),
                  ],
                }),
                new Paragraph({
                  spacing: { before: 30, after: 30 },
                  bullet: { level: 0 },
                  children: [
                    smallRun('Each outcome category produces a normalized sub-index (0\u20131). The three sub-indices are averaged to produce an '),
                    smallRun('Ecosystem Condition Index', { bold: true }),
                    smallRun('.'),
                  ],
                }),
                smallBulletItem([smallRun('This structured rollup supports interpretation, comparison across sites, and decision-making.')]),
              ],
            }),

            // Right column: Typical Uses
            new TableCell({
              borders: NO_BORDERS,
              margins: { top: 40, bottom: 40, left: 100, right: 40 },
              width: { size: 50, type: WidthType.PERCENTAGE },
              verticalAlign: VerticalAlign.TOP,
              children: [
                new Paragraph({
                  spacing: { before: 100, after: 60 },
                  border: {
                    bottom: { style: BorderStyle.SINGLE, size: 1, color: BLUE_DARK, space: 2 },
                  },
                  children: [
                    new TextRun({ text: 'Typical Uses', bold: true, size: HEADING_SIZE, color: BLUE_DARK, font: FONT }),
                  ],
                }),
                new Paragraph({
                  spacing: { before: 30, after: 30 },
                  bullet: { level: 0 },
                  children: [
                    smallRun('Ecosystem restoration planning and feasibility studies: ', { bold: true }),
                    smallRun('Identify limiting functions, compare reaches, and support development of alternatives.'),
                  ],
                }),
                new Paragraph({
                  spacing: { before: 30, after: 30 },
                  bullet: { level: 0 },
                  children: [
                    smallRun('Project prioritization: ', { bold: true }),
                    smallRun('Screening and Rapid tiers support ranking and selection across multiple candidate sites.'),
                  ],
                }),
                new Paragraph({
                  spacing: { before: 30, after: 30 },
                  bullet: { level: 0 },
                  children: [
                    smallRun('Planning and regulatory documentation: ', { bold: true }),
                    smallRun('Consistent structure for reporting and communicating findings.'),
                  ],
                }),
                new Paragraph({
                  spacing: { before: 30, after: 30 },
                  bullet: { level: 0 },
                  children: [
                    smallRun('Design support and monitoring: ', { bold: true }),
                    smallRun('Detailed tier supports design decisions and performance evaluation when higher certainty is required.'),
                  ],
                }),
              ],
            }),
          ],
        }),
      ],
    }),
  );

  // Resources
  elements.push(sectionHeading('Resources'));

  elements.push(
    new Paragraph({
      spacing: { before: 40, after: 40 },
      bullet: { level: 0 },
      children: [
        smallRun('Website: ', { bold: true }),
        new ExternalHyperlink({
          link: 'https://usace-wrises.github.io/staf/',
          children: [
            new TextRun({ text: 'usace-wrises.github.io/staf', size: SMALL_SIZE, color: BLUE_DARK, font: FONT, underline: {} }),
          ],
        }),
      ],
    }),
  );

  elements.push(
    new Paragraph({
      spacing: { before: 40, after: 40 },
      bullet: { level: 0 },
      children: [
        smallRun('Review of Assessments: ', { bold: true }),
        smallRun('Stepchinski, L. M., McKay, S. K., Harris, A. E., & Menichino, G. T. (2025). '),
        new ExternalHyperlink({
          link: 'https://doi.org/10.1111/1752-1688.70056',
          children: [
            new TextRun({ text: 'A Review of Stream Assessment Methods in the United States', size: SMALL_SIZE, color: BLUE_DARK, font: FONT, underline: {} }),
          ],
        }),
        smallRun('. Journal of the American Water Resources Association, 61.'),
      ],
    }),
  );

  elements.push(
    new Paragraph({
      spacing: { before: 40, after: 40 },
      bullet: { level: 0 },
      children: [
        smallRun('Stream functions paper: ', { bold: true }),
        smallRun('Stepchinski, L. M., McKay, S. K., & Menichino, G. T. (In review). '),
        new ExternalHyperlink({
          link: 'https://emrrp.el.erdc.dren.mil/webinars/2024/07/2024-07-22-1300ct-Stepchinski-Menichino-SlideDeck.pdf',
          children: [
            new TextRun({ text: 'Synthesis and inventory of stream functions', size: SMALL_SIZE, color: BLUE_DARK, font: FONT, underline: {} }),
          ],
        }),
        smallRun('. Manuscript submitted for publication.'),
      ],
    }),
  );

  elements.push(
    new Paragraph({
      spacing: { before: 40, after: 40 },
      bullet: { level: 0 },
      children: [
        smallRun('Tiered approach paper: ', { bold: true }),
        smallRun('Stepchinski, L. M., McKay, S. K., & Menichino, G. T. (In review). '),
        new ExternalHyperlink({
          link: 'https://emrrp.el.erdc.dren.mil/factsheets/2025-11/FY26_TieredApproachtoAssessingStreamEcosystemCondition_Menichino_2025-09-25.pdf',
          children: [
            new TextRun({ text: 'Tiered Approach to Assessing Stream Ecosystem Condition', size: SMALL_SIZE, color: BLUE_DARK, font: FONT, underline: {} }),
          ],
        }),
        smallRun('. Manuscript submitted for publication.'),
      ],
    }),
  );

  elements.push(
    new Paragraph({
      spacing: { before: 40, after: 40 },
      bullet: { level: 0 },
      children: [
        smallRun('SFARI Rapid Assessment: ', { bold: true }),
        smallRun('David, G. C., Stepchinski, L. M., Wiest, S. R., & Menichino, G. T. (In review). '),
        new ExternalHyperlink({
          link: 'https://emrrp.el.erdc.dren.mil/webinars/2025/08/2025-08-28_EMRRP_Webinar_SFARI.pdf',
          children: [
            new TextRun({ text: 'Stream Functions Assessment and Rapid Index (SFARI): A nationally applicable, rapid, function-based stream assessment', size: SMALL_SIZE, color: BLUE_DARK, font: FONT, underline: {} }),
          ],
        }),
        smallRun('. ERDC/EMRRP Technical Report. Vicksburg, MS: Army Engineer Research and Development Center.'),
      ],
    }),
  );

  // Bottom summary box
  elements.push(
    new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      rows: [
        new TableRow({
          children: [
            new TableCell({
              shading: { type: ShadingType.CLEAR, fill: BLUE_DARK, color: WHITE },
              borders: NO_BORDERS,
              margins: { top: 80, bottom: 80, left: 120, right: 120 },
              width: { size: 100, type: WidthType.PERCENTAGE },
              children: [
                new Paragraph({
                  alignment: AlignmentType.CENTER,
                  children: [
                    new TextRun({
                      text: 'STAF provides a clear tiered pathway, a consistent set of stream functions, and a structured scoring approach that rolls function results into physical, chemical, and biological outcomes and an overall Ecosystem Condition Index. It helps teams scale effort to need, keep evaluations comparable, and communicate results clearly.',
                      size: BODY_SIZE,
                      color: WHITE,
                      font: FONT,
                    }),
                  ],
                }),
              ],
            }),
          ],
        }),
      ],
    }),
  );

  return elements;
}

/* ────────────────────────────────────────────
   HTML factsheet generator
   ──────────────────────────────────────────── */

function generateHtml(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>STAF Factsheet</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@400;600;700;900&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: 'Source Sans 3', 'Segoe UI', sans-serif;
    background: #e8e8e8;
    color: #222;
    line-height: 1.45;
    font-size: 11.5pt;
  }

  .page {
    width: 8.5in;
    min-height: 11in;
    background: #fff;
    margin: 0.5in auto;
    padding: 0;
    box-shadow: 0 2px 16px rgba(0,0,0,0.12);
    overflow: hidden;
    position: relative;
    page-break-after: always;
  }

  /* === SHARED HEADER === */
  .page-header {
    background: #2b5a7c;
    color: #fff;
    padding: 0.35in 0.55in 0.3in;
  }
  .page-header h1 {
    font-size: 22pt;
    font-weight: 900;
    letter-spacing: -0.3px;
    margin-bottom: 2px;
  }
  .page-header .subtitle {
    font-size: 11pt;
    font-weight: 400;
    opacity: 0.9;
  }

  /* === PAGE 1 === */
  .p1-body {
    padding: 0.3in 0.55in 0.35in;
  }

  .section-label {
    font-size: 12pt;
    font-weight: 700;
    color: #2b5a7c;
    margin-bottom: 6px;
    margin-top: 14px;
    border-bottom: 2px solid #2b5a7c;
    padding-bottom: 3px;
  }
  .section-label:first-child { margin-top: 0; }

  .need-text {
    font-size: 10.5pt;
    line-height: 1.5;
    color: #333;
    margin-bottom: 4px;
  }

  /* Tier diagram */
  .tier-diagram {
    display: flex;
    gap: 10px;
    margin-top: 10px;
    margin-bottom: 8px;
  }
  .tier-card {
    flex: 1;
    border-radius: 8px;
    padding: 12px 14px;
    font-size: 9.5pt;
    line-height: 1.45;
  }
  .tier-card.screening {
    background: #f0f4f7;
    border: 1.5px solid #b8cdd9;
  }
  .tier-card.rapid {
    background: #e6e6e6;
    border: 1.5px solid #aaa;
  }
  .tier-card.detailed {
    background: #d0d0d0;
    border: 1.5px solid #888;
  }
  .tier-card h3 {
    font-size: 11pt;
    font-weight: 900;
    text-transform: uppercase;
    margin-bottom: 6px;
    letter-spacing: 0.3px;
  }
  .tier-card .field {
    margin-bottom: 4px;
  }
  .tier-card .field strong {
    font-weight: 700;
  }

  /* Effort arrow */
  .effort-arrow {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 6px 0 4px;
    font-size: 9pt;
    color: #555;
  }
  .effort-arrow .arrow-line {
    flex: 1;
    height: 2px;
    background: linear-gradient(to right, #b8cdd9, #555);
    margin: 0 10px;
    position: relative;
  }
  .effort-arrow .arrow-line::after {
    content: '';
    position: absolute;
    right: -6px;
    top: -4px;
    border-left: 8px solid #555;
    border-top: 5px solid transparent;
    border-bottom: 5px solid transparent;
  }
  .effort-caption {
    text-align: center;
    font-size: 9pt;
    color: #555;
    margin-bottom: 10px;
  }

  /* Callout boxes */
  .callout-box {
    border: 2px solid #2b5a7c;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 10pt;
    line-height: 1.4;
    margin-bottom: 6px;
  }
  .callout-box strong {
    font-weight: 700;
  }

  /* Comparable results box */
  .comparable-box {
    border: 2px solid #2b5a7c;
    border-radius: 6px;
    padding: 10px 14px;
    font-size: 10pt;
    line-height: 1.4;
  }
  .comparable-box > .box-heading {
    font-weight: 700;
    margin-bottom: 4px;
    font-size: 10pt;
  }
  .comparable-box > .box-intro {
    margin-bottom: 8px;
    font-size: 9.5pt;
    color: #333;
  }

  /* Outcome cards */
  .outcome-cards {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;
  }
  .outcome-card {
    flex: 1;
    background: #fafafa;
    border: 1px solid #ccc;
    border-radius: 4px;
    padding: 8px 10px;
    font-size: 8.5pt;
    line-height: 1.4;
  }
  .outcome-card.physical  { border-top: 4px solid #4a90a4; }
  .outcome-card.chemical  { border-top: 4px solid #6b8e23; }
  .outcome-card.biological { border-top: 4px solid #b8860b; }
  .outcome-card.ecosystem { border-top: 4px solid #2b5a7c; }
  .outcome-card h4 {
    font-size: 9pt;
    font-weight: 700;
    margin-bottom: 4px;
  }
  .outcome-card.physical h4  { color: #4a90a4; }
  .outcome-card.chemical h4  { color: #6b8e23; }
  .outcome-card.biological h4 { color: #b8860b; }
  .outcome-card.ecosystem h4 { color: #2b5a7c; }
  .outcome-card p { color: #333; margin-bottom: 4px; }

  /* === PAGE 2 === */
  .p2-body {
    padding: 0.25in 0.55in 0.35in;
  }

  .purpose-box {
    background: #f5f8fa;
    border-left: 4px solid #2b5a7c;
    padding: 10px 14px;
    margin-top: 8px;
    margin-bottom: 4px;
    font-size: 9.5pt;
    line-height: 1.5;
    color: #333;
  }
  .purpose-box strong {
    display: block;
    font-size: 10.5pt;
    color: #2b5a7c;
    margin-bottom: 3px;
  }
  .objectives-list {
    padding-left: 18px;
    margin-top: 4px;
  }
  .objectives-list li {
    font-size: 9.5pt;
    line-height: 1.45;
    margin-bottom: 3px;
    color: #333;
  }

  .two-col {
    display: flex;
    gap: 20px;
    margin-top: 6px;
  }
  .col {
    flex: 1;
  }
  .col h3 {
    font-size: 10.5pt;
    font-weight: 700;
    color: #2b5a7c;
    margin-bottom: 4px;
    margin-top: 10px;
  }
  .col h3:first-child { margin-top: 0; }
  .col p, .col li {
    font-size: 9.5pt;
    line-height: 1.45;
    color: #333;
  }
  .col ul {
    padding-left: 16px;
    margin-top: 3px;
  }
  .col li {
    margin-bottom: 3px;
  }

  .resources-list {
    list-style: none;
    padding-left: 0;
    margin-top: 3px;
  }
  .resources-list li {
    font-size: 8.5pt;
    line-height: 1.45;
    margin-bottom: 4px;
    color: #333;
  }
  .resources-list a {
    color: #2b5a7c;
  }

  .bottom-summary {
    background: #2b5a7c;
    color: #fff;
    border-radius: 6px;
    padding: 10px 16px;
    margin-top: 12px;
    font-size: 9.5pt;
    line-height: 1.45;
    text-align: center;
  }

  /* Print */
  @media print {
    body { background: #fff; }
    .page { box-shadow: none; margin: 0; }
  }
  @page { size: letter; margin: 0; }
</style>
</head>
<body>

<!-- ========== PAGE 1 ========== -->
<div class="page">
  <div class="page-header">
    <h1>Stream Tiered Assessment Framework (STAF)</h1>
    <div class="subtitle">A structured approach to assessing stream ecosystem condition</div>
  </div>

  <div class="p1-body">
    <div class="section-label">Background and Need</div>
    <p class="need-text">
      USACE planners, engineers, and practitioners need tools for holistically assessing stream structure, function, and dynamic processes. Existing approaches have often relied on species-specific habitat models that focus narrowly on single taxa and do not capture ecosystem-scale outcomes. At the same time, the Civil Works planning process (SMART Planning) imposes different modeling needs as projects progress, from rapid site screening with sparse data (1\u20133 months) through conceptual design (12\u201320 months) to final design and post-construction monitoring. There is no standardized, nationally applicable framework that scales assessment effort to project phase while keeping results comparable.
    </p>
    <p class="need-text" style="margin-top:4px;">
      A review of 188 stream assessment methods in the U.S. found that most methods do not evaluate the full range of ecosystem functions, there is limited guidance on matching effort to project phase, and results are difficult to compare across methods, projects, and regions.
    </p>

    <div class="section-label" style="margin-top:16px;">The Framework</div>

    <div class="tier-diagram">
      <div class="tier-card screening">
        <h3>Screening Tier</h3>
        <div class="field"><strong>Goal:</strong> Provides a broad, watershed or landscape-scale snapshot. Identifies constraints, opportunities, and where additional effort may be needed.</div>
        <div class="field"><strong>Data sources:</strong> Desktop GIS, existing data review, limited site photos, and brief recon where available.</div>
        <div class="field"><strong>Effort:</strong> Minutes to hours.</div>
        <div class="field"><strong>Uses:</strong> Scoping and site screening.</div>
      </div>
      <div class="tier-card rapid">
        <h3>Rapid Tier</h3>
        <div class="field"><strong>Goal:</strong> Refines understanding of site conditions. Field evidence supports planning decisions and alternatives comparison.</div>
        <div class="field"><strong>Data sources:</strong> Focused field observations, many site photos, limited modeling or lab data, desktop GIS, existing data.</div>
        <div class="field"><strong>Effort:</strong> Hours to days.</div>
        <div class="field"><strong>Uses:</strong> Design, comparing alternatives.</div>
      </div>
      <div class="tier-card detailed">
        <h3>Detailed Tier</h3>
        <div class="field"><strong>Goal:</strong> Provides high confidence, site-specific data to support final design decisions and performance evaluation.</div>
        <div class="field"><strong>Data sources:</strong> Intensive field data collection, extensive modeling or laboratory analysis, site photos, desktop GIS, existing data.</div>
        <div class="field"><strong>Effort:</strong> Days to weeks.</div>
        <div class="field"><strong>Uses:</strong> Final design, post-construction monitoring, regional studies.</div>
      </div>
    </div>

    <div class="effort-arrow">
      <span>lower</span>
      <div class="arrow-line"></div>
      <span style="margin-right:10px;">higher</span>
    </div>
    <div class="effort-caption">Effort, data needs, confidence</div>

    <div class="callout-box">
      <strong>Common foundation:</strong> Stream functions are consistent across all tiers. The functions are comprehensive and broadly applicable to a wide range of streams.
    </div>

    <div class="comparable-box">
      <div class="box-heading">Comparable results: Outcomes and Ecosystem Condition</div>
      <div class="box-intro"><strong>Outcomes</strong> are observable or quantifiable results that are linked to how one or more stream functions operate.</div>

      <div class="outcome-cards">
        <div class="outcome-card physical">
          <h4>Physical Outcomes</h4>
          <p>Physical outcomes are measurable results of hydrologic, hydraulic, geomorphic, and habitat-forming processes. They describe the physical structure of the stream and how water and sediment move through the system.</p>
          <p><strong>Examples:</strong> floodplain inundation frequency, channel stability, sediment transport balance, habitat unit distribution, substrate composition, and large wood abundance.</p>
        </div>
        <div class="outcome-card chemical">
          <h4>Chemical Outcomes</h4>
          <p>Chemical outcomes are measurable results of water chemistry and biogeochemical processes. They describe how chemical conditions support or limit aquatic life and ecosystem processes.</p>
          <p><strong>Examples:</strong> dissolved oxygen, nutrient concentrations, temperature regime, pH, contaminant levels, and organic matter decomposition rates.</p>
        </div>
        <div class="outcome-card biological">
          <h4>Biological Outcomes</h4>
          <p>Biological outcomes are measurable characteristics of aquatic and riparian communities. They describe the presence, abundance, diversity, and functional roles of organisms in the system.</p>
          <p><strong>Examples:</strong> fish assemblage composition, macroinvertebrate diversity, species richness, presence of sensitive taxa, and functional feeding group distribution.</p>
        </div>
      </div>

      <div class="outcome-card ecosystem" style="width:100%;">
        <h4>Ecosystem Condition</h4>
        <p>The overall state of a stream system, based on the combined performance of physical, chemical, and biological outcomes relative to expected or reference conditions. It indicates how well the system sustains ecological function and resilience over time.</p>
      </div>
    </div>

  </div>
</div>

<!-- ========== PAGE 2 ========== -->
<div class="page">
  <div class="page-header">
    <h1>Stream Tiered Assessment Framework (STAF)</h1>
    <div class="subtitle">Purpose, Functions, Scoring, and Uses</div>
  </div>

  <div class="p2-body">
    <div class="purpose-box">
      <strong>Purpose and Objectives</strong>
      STAF provides a nested toolkit of stream assessment methods that align with project planning needs at varying levels of effort. The framework provides a common, transferable structure for stream assessment that can be applied at multiple scales and tailored to local needs.
      <ul class="objectives-list">
        <li>Develop a common and transferable structure for stream assessment, applicable at multiple scales and tailored to local needs.</li>
        <li>Compile tools for assessing stream outcomes at three scales: screening-level analyses, rapid field assessment, and detailed analyses informed by empirical data collection.</li>
        <li>Develop select tools to fill gaps in the existing toolbox.</li>
        <li>Demonstrate application of the multi-scale framework as a go-by for users.</li>
      </ul>
    </div>

    <div class="two-col">
      <div class="col">
        <h3>Common Stream Functions</h3>
        <ul>
          <li>All tiers assess the same set of stream functions.</li>
          <li>The tiers differ in how each function is evaluated and documented.</li>
          <li>This improves consistency across projects and makes it easier to compare results across sites, watersheds, and study phases.</li>
        </ul>

        <h3>Scoring Approach</h3>
        <ul>
          <li>Each stream function is evaluated using tier-appropriate evidence and scored on a 0\u201315 scale.</li>
          <li>Function scores are grouped into three outcome categories: <strong>physical</strong> (hydrology, hydraulics, geomorphology), <strong>chemical</strong> (water chemistry, biogeochemical processes), and <strong>biological</strong> (aquatic and riparian communities).</li>
          <li>Each outcome category produces a normalized sub-index (0\u20131). The three sub-indices are averaged to produce an <strong>Ecosystem Condition Index</strong>.</li>
          <li>This structured rollup supports interpretation, comparison across sites, and decision-making.</li>
        </ul>
      </div>

      <div class="col">
        <h3>Typical Uses</h3>
        <ul>
          <li><strong>Ecosystem restoration planning and feasibility studies:</strong> Identify limiting functions, compare reaches, and support development of alternatives.</li>
          <li><strong>Project prioritization:</strong> Screening and Rapid tiers support ranking and selection across multiple candidate sites.</li>
          <li><strong>Planning and regulatory documentation:</strong> Consistent structure for reporting and communicating findings.</li>
          <li><strong>Design support and monitoring:</strong> Detailed tier supports design decisions and performance evaluation when higher certainty is required.</li>
        </ul>

        <h3>Resources</h3>
        <ul class="resources-list">
          <li><strong>Website:</strong> <a href="https://usace-wrises.github.io/staf/">usace-wrises.github.io/staf</a></li>
          <li><strong>Review of Assessments:</strong> Stepchinski, L. M., McKay, S. K., Harris, A. E., &amp; Menichino, G. T. (2025). <a href="https://doi.org/10.1111/1752-1688.70056">A Review of Stream Assessment Methods in the United States</a>. Journal of the American Water Resources Association, 61.</li>
          <li><strong>Stream functions paper:</strong> Stepchinski, L. M., McKay, S. K., &amp; Menichino, G. T. (In review). <a href="https://emrrp.el.erdc.dren.mil/webinars/2024/07/2024-07-22-1300ct-Stepchinski-Menichino-SlideDeck.pdf">Synthesis and inventory of stream functions</a>. Manuscript submitted for publication.</li>
          <li><strong>Tiered approach paper:</strong> Stepchinski, L. M., McKay, S. K., &amp; Menichino, G. T. (In review). <a href="https://emrrp.el.erdc.dren.mil/factsheets/2025-11/FY26_TieredApproachtoAssessingStreamEcosystemCondition_Menichino_2025-09-25.pdf">Tiered Approach to Assessing Stream Ecosystem Condition</a>. Manuscript submitted for publication.</li>
          <li><strong>SFARI Rapid Assessment:</strong> David, G. C., Stepchinski, L. M., Wiest, S. R., &amp; Menichino, G. T. (In review). <a href="https://emrrp.el.erdc.dren.mil/webinars/2025/08/2025-08-28_EMRRP_Webinar_SFARI.pdf">Stream Functions Assessment and Rapid Index (SFARI): A nationally applicable, rapid, function-based stream assessment</a>. ERDC/EMRRP Technical Report. Vicksburg, MS: Army Engineer Research and Development Center.</li>
        </ul>
      </div>
    </div>

    <div class="bottom-summary">
      STAF provides a clear tiered pathway, a consistent set of stream functions, and a structured scoring approach that rolls function results into physical, chemical, and biological outcomes and an overall Ecosystem Condition Index. It helps teams scale effort to need, keep evaluations comparable, and communicate results clearly.
    </div>

  </div>
</div>

</body>
</html>`;
}

/* ────────────────────────────────────────────
   Build & write the document
   ──────────────────────────────────────────── */

async function main() {
  const page1 = buildPage1();
  const page2 = buildPage2();

  const doc = new Document({
    styles: {
      default: {
        document: {
          run: {
            font: FONT,
            size: BODY_SIZE,
            color: BODY_COLOR,
          },
        },
      },
    },
    sections: [
      {
        properties: {
          page: {
            size: {
              width: convertInchesToTwip(8.5),
              height: convertInchesToTwip(11),
            },
            margin: {
              top: convertInchesToTwip(0.35),
              bottom: convertInchesToTwip(0.35),
              left: convertInchesToTwip(0.55),
              right: convertInchesToTwip(0.55),
            },
          },
        },
        children: [...page1],
      },
      {
        properties: {
          page: {
            size: {
              width: convertInchesToTwip(8.5),
              height: convertInchesToTwip(11),
            },
            margin: {
              top: convertInchesToTwip(0.35),
              bottom: convertInchesToTwip(0.35),
              left: convertInchesToTwip(0.55),
              right: convertInchesToTwip(0.55),
            },
          },
        },
        children: [...page2],
      },
    ],
  });

  const buffer = await Packer.toBuffer(doc);

  const root = process.cwd();
  const outDir = path.join(root, 'docs', 'assets', 'docs');
  await fs.mkdir(outDir, { recursive: true });

  const docxPath = path.join(outDir, 'STAF_Factsheet.docx');
  await fs.writeFile(docxPath, buffer);
  console.log(`Factsheet DOCX written to ${docxPath}`);

  // HTML factsheet
  const htmlContent = generateHtml();
  const htmlPath = path.join(outDir, 'STAF_Factsheet.html');
  await fs.writeFile(htmlPath, htmlContent, 'utf-8');
  console.log(`Factsheet HTML written to ${htmlPath}`);

}

main().catch((err) => {
  console.error('Failed to generate factsheet:', err);
  process.exit(1);
});
