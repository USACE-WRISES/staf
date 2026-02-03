import { z } from 'zod';

export const TierSchema = z.enum(['screening', 'rapid', 'detailed']);
export const ScoringTypeSchema = z.enum([
  'categorical',
  'thresholds',
  'curve',
  'formula',
  'binary',
  'lookup',
]);

export const OutputSpecSchema = z.discriminatedUnion('kind', [
  z.object({
    kind: z.literal('rating'),
    ratingScaleId: z.string(),
  }),
  z.object({
    kind: z.literal('points'),
    min: z.number(),
    max: z.number(),
    unit: z.string().optional(),
  }),
  z.object({
    kind: z.literal('number'),
    unit: z.string().optional(),
  }),
]);

const RuleSchema: z.ZodType<any> = z.lazy(() =>
  z.discriminatedUnion('kind', [
    z.object({
      kind: z.literal('range'),
      var: z.string(),
      min: z.number().optional(),
      max: z.number().optional(),
    }),
    z.object({
      kind: z.literal('comparison'),
      var: z.string(),
      op: z.enum(['<', '<=', '>', '>=', '==', '!=']),
      value: z.number(),
    }),
    z.object({
      kind: z.literal('and'),
      rules: z.array(RuleSchema).min(1),
    }),
    z.object({
      kind: z.literal('or'),
      rules: z.array(RuleSchema).min(1),
    }),
    z.object({
      kind: z.literal('textOnly'),
      text: z.string(),
    }),
  ])
);

export { RuleSchema };

export const RubricLevelSchema = z.object({
  label: z.string(),
  ratingId: z.string().optional(),
  criteriaMarkdown: z.string().optional(),
  rules: z.array(RuleSchema).optional(),
});

export const ThresholdBandSchema = z.object({
  label: z.string(),
  rawScore: z.string().optional(),
  ratingId: z.string().optional(),
  criteriaMarkdown: z.string().optional(),
  min: z.number().optional(),
  max: z.number().optional(),
});

export const FormulaVariableSchema = z.object({
  key: z.string(),
  label: z.string().optional(),
  unit: z.string().optional(),
});

export const CategoricalScoringSchema = z.object({
  type: z.literal('categorical'),
  ratingScaleId: z.string(),
  output: OutputSpecSchema.optional(),
  rubric: z.object({
    levels: z.array(RubricLevelSchema).min(1),
  }),
});

export const ThresholdsScoringSchema = z.object({
  type: z.literal('thresholds'),
  output: OutputSpecSchema.optional(),
  rubric: z.object({
    bands: z.array(ThresholdBandSchema).min(1),
  }),
});

export const CurveScoringSchema = z.object({
  type: z.literal('curve'),
  scoringMethod: z.string().optional(),
  ratingMapping: z.string().optional(),
  rubric: z.object({
    curveSetRefs: z.array(z.string()),
  }),
});

export const FormulaScoringSchema = z.object({
  type: z.literal('formula'),
  output: OutputSpecSchema.optional(),
  rubric: z.object({
    expression: z.string(),
    variables: z.array(FormulaVariableSchema),
    outputMapping: z.string().optional(),
  }),
});

export const BinaryScoringSchema = z.object({
  type: z.literal('binary'),
  output: OutputSpecSchema.optional(),
  rubric: z
    .object({
      trueLabel: z.string().optional(),
      falseLabel: z.string().optional(),
      criteriaMarkdown: z.string().optional(),
    })
    .optional(),
});

export const LookupScoringSchema = z.object({
  type: z.literal('lookup'),
  output: OutputSpecSchema.optional(),
  rubric: z
    .object({
      table: z.array(z.object({ input: z.string(), output: z.string() })),
    })
    .optional(),
});

export const ScoringSchema = z.discriminatedUnion('type', [
  CategoricalScoringSchema,
  ThresholdsScoringSchema,
  CurveScoringSchema,
  FormulaScoringSchema,
  BinaryScoringSchema,
  LookupScoringSchema,
]);

export const ProfileSchema = z.object({
  profileId: z.string(),
  tier: TierSchema,
  status: z.enum(['active', 'draft', 'retired']),
  recommended: z.boolean().optional(),
  scoring: ScoringSchema,
  curveIntegration: z.object({
    enabled: z.boolean(),
    curveSetRefs: z.array(z.string()),
  }),
});

export const MetricInputSchema = z.object({
  label: z.string(),
  type: z.string(),
  unit: z.string().optional(),
  source: z.string().optional(),
  required: z.boolean().optional(),
  helpMarkdown: z.string().optional(),
});

export const MetricDetailSchema = z.object({
  schemaVersion: z.number().optional(),
  metricId: z.string(),
  name: z.string(),
  shortName: z.string().optional(),
  discipline: z.string().optional(),
  function: z.string().optional(),
  functionStatement: z.string().optional(),
  descriptionMarkdown: z.string().optional(),
  methodContextMarkdown: z.string().optional(),
  howToMeasureMarkdown: z.string().optional(),
  inputs: z.array(MetricInputSchema).optional(),
  profiles: z.array(ProfileSchema).min(1),
  references: z.array(z.string()).optional(),
  tags: z.array(z.string()).optional(),
  status: z.string().optional(),
  version: z.string().optional(),
});

export const ProfileSummarySchema = z.object({
  profileId: z.string(),
  scoringType: ScoringTypeSchema,
  scoringShape: z.string().optional(),
  rawOutput: z.string().optional(),
  normalizedOutput: z.string().optional(),
  curveSetCount: z.number().optional(),
});

export const MetricIndexEntrySchema = z.object({
  metricId: z.string(),
  name: z.string(),
  shortName: z.string().optional(),
  discipline: z.string().optional(),
  function: z.string().optional(),
  category: z.string().optional(),
  tags: z.array(z.string()).optional(),
  status: z.string().optional(),
  minimumTier: TierSchema.optional(),
  profileAvailability: z.object({
    screening: z.boolean(),
    rapid: z.boolean(),
    detailed: z.boolean(),
  }),
  recommendedTiers: z.array(TierSchema),
  inputsSummary: z.object({
    sources: z.array(z.string()),
    effort: z.string(),
    primaryUnit: z.string().optional(),
  }),
  profileSummaries: z.object({
    screening: ProfileSummarySchema.optional(),
    rapid: ProfileSummarySchema.optional(),
    detailed: ProfileSummarySchema.optional(),
  }),
  curvesSummary: z.object({
    totalCurveSetCount: z.number(),
    byTier: z.record(TierSchema, z.number()),
  }),
  detailsRef: z.string(),
});

export const MetricIndexSchema = z.object({
  schemaVersion: z.number().optional(),
  metrics: z.array(MetricIndexEntrySchema),
});

export const RatingScaleLevelSchema = z.object({
  id: z.string(),
  label: z.string(),
  order: z.number(),
  score: z.number().optional(),
});

export const RatingScaleSchema = z.object({
  ratingScaleId: z.string(),
  name: z.string().optional(),
  levels: z.array(RatingScaleLevelSchema).min(1),
});

export const RatingScalesSchema = z.object({
  schemaVersion: z.number(),
  ratingScales: z.array(RatingScaleSchema).min(1),
});

export const CurvePointSchema = z.object({
  x: z.union([z.string(), z.number()]),
  y: z.number(),
  description: z.string().optional(),
});

export const CurveLayerSchema = z.object({
  id: z.string(),
  name: z.string(),
  points: z.array(CurvePointSchema).min(1),
});

export const CurveSchema = z.object({
  curveId: z.string(),
  name: z.string(),
  xType: z.enum(['qualitative', 'quantitative']),
  units: z.string().optional(),
  layers: z.array(CurveLayerSchema).min(1),
  activeLayerId: z.string().nullable().optional(),
});

export const CurveSetSchema = z.object({
  schemaVersion: z.number().optional(),
  curveSetId: z.string(),
  metricId: z.string(),
  tier: TierSchema.optional(),
  name: z.string().optional(),
  axes: z
    .object({
      xLabel: z.string().optional(),
      yLabel: z.string().optional(),
      xUnit: z.string().optional(),
      yUnit: z.string().optional(),
    })
    .optional(),
  curves: z.array(CurveSchema).min(1),
});

export const parseMetricDetail = (data: unknown) => MetricDetailSchema.parse(data);
export const parseMetricIndexEntry = (data: unknown) => MetricIndexEntrySchema.parse(data);
export const parseMetricIndex = (data: unknown) => MetricIndexSchema.parse(data);
export const parseRatingScales = (data: unknown) => RatingScalesSchema.parse(data);
export const parseCurveSet = (data: unknown) => CurveSetSchema.parse(data);

