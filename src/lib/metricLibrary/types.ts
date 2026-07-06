export type Tier = 'screening' | 'rapid' | 'detailed';

export type ScoringType =
  | 'categorical'
  | 'thresholds'
  | 'curve'
  | 'formula'
  | 'binary'
  | 'lookup';

export type OutputSpec =
  | { kind: 'rating'; ratingScaleId: string }
  | { kind: 'points'; min: number; max: number; unit?: string }
  | { kind: 'number'; unit?: string };

export type RuleRange = {
  kind: 'range';
  var: string;
  min?: number;
  max?: number;
};

export type RuleComparison = {
  kind: 'comparison';
  var: string;
  op: '<' | '<=' | '>' | '>=' | '==' | '!=';
  value: number;
};

export type RuleAnd = {
  kind: 'and';
  rules: Rule[];
};

export type RuleOr = {
  kind: 'or';
  rules: Rule[];
};

export type RuleText = {
  kind: 'textOnly';
  text: string;
};

export type Rule = RuleRange | RuleComparison | RuleAnd | RuleOr | RuleText;

export type RubricLevel = {
  label: string;
  ratingId?: string;
  criteriaMarkdown?: string;
  rules?: Rule[];
};

export type ThresholdBand = {
  label: string;
  rawScore?: string;
  ratingId?: string;
  criteriaMarkdown?: string;
  min?: number;
  max?: number;
};

export type FormulaVariable = {
  key: string;
  label?: string;
  unit?: string;
};

export type CategoricalScoring = {
  type: 'categorical';
  ratingScaleId: string;
  output?: OutputSpec;
  rubric: {
    levels: RubricLevel[];
  };
};

export type ThresholdsScoring = {
  type: 'thresholds';
  output?: OutputSpec;
  rubric: {
    bands: ThresholdBand[];
  };
};

export type CurveScoring = {
  type: 'curve';
  scoringMethod?: string;
  ratingMapping?: string;
  rubric: {
    curveSetRefs: string[];
  };
};

export type FormulaScoring = {
  type: 'formula';
  output?: OutputSpec;
  rubric: {
    expression: string;
    variables: FormulaVariable[];
    outputMapping?: string;
  };
};

export type BinaryScoring = {
  type: 'binary';
  output?: OutputSpec;
  rubric?: {
    trueLabel?: string;
    falseLabel?: string;
    criteriaMarkdown?: string;
  };
};

export type LookupScoring = {
  type: 'lookup';
  output?: OutputSpec;
  rubric?: {
    table: Array<{ input: string; output: string }>;
  };
};

export type Scoring =
  | CategoricalScoring
  | ThresholdsScoring
  | CurveScoring
  | FormulaScoring
  | BinaryScoring
  | LookupScoring;

export type Profile = {
  profileId: string;
  tier: Tier;
  status: 'active' | 'draft' | 'retired';
  recommended?: boolean;
  scoring: Scoring;
  curveIntegration: {
    enabled: boolean;
    curveSetRefs: string[];
  };
};

export type MetricInput = {
  label: string;
  type: string;
  unit?: string;
  source?: string;
  required?: boolean;
  helpMarkdown?: string;
};

export type MetricDetail = {
  schemaVersion?: number;
  metricId: string;
  name: string;
  shortName?: string;
  discipline?: string;
  function?: string;
  functionStatement?: string;
  descriptionMarkdown?: string;
  methodContextMarkdown?: string;
  howToMeasureMarkdown?: string;
  inputs?: MetricInput[];
  profiles: Profile[];
  references?: string[];
  tags?: string[];
  status?: string;
  version?: string;
};

export type ProfileSummary = {
  profileId: string;
  scoringType: ScoringType;
  scoringShape?: string;
  rawOutput?: string;
  normalizedOutput?: string;
  curveSetCount?: number;
};

export type MetricIndexEntry = {
  metricId: string;
  name: string;
  shortName?: string;
  discipline?: string;
  function?: string;
  category?: string;
  tags?: string[];
  status?: string;
  minimumTier?: Tier;
  profileAvailability: {
    screening: boolean;
    rapid: boolean;
    detailed: boolean;
  };
  recommendedTiers: Tier[];
  inputsSummary: {
    sources: string[];
    effort: string;
    primaryUnit?: string;
  };
  profileSummaries: {
    screening?: ProfileSummary;
    rapid?: ProfileSummary;
    detailed?: ProfileSummary;
  };
  curvesSummary: {
    totalCurveSetCount: number;
    byTier: Record<Tier, number>;
  };
  detailsRef: string;
};

export type RatingScaleLevel = {
  id: string;
  label: string;
  order: number;
  score?: number;
};

export type RatingScale = {
  ratingScaleId: string;
  name?: string;
  levels: RatingScaleLevel[];
};

export type RatingScalesFile = {
  schemaVersion: number;
  ratingScales: RatingScale[];
};

export type CurvePoint = {
  x: string | number;
  y: number;
  description?: string;
};

export type CurveLayer = {
  id: string;
  name: string;
  points: CurvePoint[];
};

export type Curve = {
  curveId: string;
  name: string;
  xType: 'qualitative' | 'quantitative';
  units?: string;
  layers: CurveLayer[];
  activeLayerId?: string | null;
};

export type CurveSet = {
  schemaVersion?: number;
  curveSetId: string;
  metricId: string;
  tier?: Tier;
  name?: string;
  axes?: {
    xLabel?: string;
    yLabel?: string;
    xUnit?: string;
    yUnit?: string;
  };
  curves: Curve[];
};

