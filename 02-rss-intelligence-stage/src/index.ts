import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { saveFinalStage } from "./stage.js";

const Source = Type.Object({name:Type.String({minLength:1,maxLength:200}),url:Type.String({minLength:8,maxLength:4096})});
const Item = Type.Object({
  eventKey:Type.String({minLength:3,maxLength:500}),title:Type.String({minLength:1,maxLength:300}),priority:Type.Union([Type.Literal("S"),Type.Literal("A"),Type.Literal("B"),Type.Literal("C")]),category:Type.String({minLength:1,maxLength:160}),
  publishedAt:Type.Optional(Type.String({maxLength:64})),eventAt:Type.Optional(Type.String({maxLength:64})),summary:Type.String({minLength:1,maxLength:3000}),whyImportant:Type.String({minLength:1,maxLength:3000}),impact:Type.String({minLength:1,maxLength:3000}),opportunity:Type.Optional(Type.String({maxLength:3000})),isHighValueOpportunity:Type.Boolean(),risk:Type.Optional(Type.String({maxLength:3000})),originalTitle:Type.Optional(Type.String({maxLength:1000})),sources:Type.Array(Source,{minItems:1,maxItems:5})
});
const Accounting = Type.Object({primaryArticleId:Type.String({minLength:1,maxLength:512}),disposition:Type.Union([Type.Literal("retained"),Type.Literal("merged"),Type.Literal("filtered")]),finalEventKey:Type.Optional(Type.String({minLength:3,maxLength:500})),reason:Type.String({minLength:1,maxLength:600})});

export default defineToolPlugin({
  id:"rss-intelligence-stage-v52",
  name:"RSS Intelligence Stage V5.2",
  description:"Validate and save private V5.2 Morning/Afternoon staging data for host-only publication. Never modifies FreshRSS or public RSS files.",
  configSchema:Type.Object({}, {additionalProperties:false}),
  tools:(tool)=>[
    tool({
      name:"rss_daily_finalize_save_v52",
      label:"Stage final RSS Intelligence V5.2 result",
      description:"Validate every prepared V5.2 candidate is accounted exactly once, then save a private staged result. Does not publish public RSS and does not modify FreshRSS.",
      optional:true,
      parameters:Type.Object({
        edition:Type.Union([Type.Literal("morning"),Type.Literal("afternoon")]),
        candidateRunId:Type.String({minLength:1,maxLength:160}),
        morningBaselineId:Type.Optional(Type.String({maxLength:128})),
        generatedAt:Type.Optional(Type.String({maxLength:64})),
        title:Type.String({minLength:1,maxLength:300}),
        reportMarkdown:Type.String({minLength:1,maxLength:100000}),
        accounting:Type.Array(Accounting,{maxItems:500}),
        items:Type.Array(Item,{maxItems:500})
      }),
      execute:async(params,_config,context)=>{if(context.signal?.aborted)throw new Error("final staging aborted");return saveFinalStage(params);}
    })
  ]
});
