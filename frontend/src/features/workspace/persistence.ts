import {getWorkspacePreset,getWorkspacePresets} from "./presets";
import {WIDGET_DEFINITIONS,WORKSPACE_IDS,type WidgetId,type WidgetPlacement,type WorkspaceId,type WorkspaceLayout,type WorkspaceLayouts} from "./types";
export const WORKSPACE_SCHEMA_VERSION=1;
export const WORKSPACE_STORAGE_KEY="cryptoedge.workspace-layouts";
export interface StorageAdapter{getItem(k:string):string|null;setItem(k:string,v:string):void;removeItem(k:string):void}
export interface WorkspaceLoadResult{layouts:WorkspaceLayouts;recovered:boolean;reason:"stored"|"migrated"|"missing"|"invalid"|"storage-error"}
const record=(v:unknown):v is Record<string,unknown>=>typeof v==="object"&&v!==null&&!Array.isArray(v);
const int=(v:unknown,min:number,max:number):v is number=>Number.isInteger(v)&&Number(v)>=min&&Number(v)<=max;
const widgetId=(v:unknown):v is WidgetId=>typeof v==="string"&&Object.hasOwn(WIDGET_DEFINITIONS,v);
export const isWidgetPlacement=(v:unknown):v is WidgetPlacement=>record(v)&&widgetId(v.id)&&int(v.x,0,11)&&int(v.y,0,10000)&&int(v.width,1,12)&&int(v.height,1,100)&&Number(v.x)+Number(v.width)<=12&&(v.minWidth===undefined||int(v.minWidth,1,12))&&(v.minHeight===undefined||int(v.minHeight,1,100));
export const isWorkspaceLayout=(v:unknown,id?:WorkspaceId):v is WorkspaceLayout=>record(v)&&WORKSPACE_IDS.includes(v.id as WorkspaceId)&&(id===undefined||v.id===id)&&Array.isArray(v.widgets)&&v.widgets.length<=50&&v.widgets.every(isWidgetPlacement)&&new Set(v.widgets.map(x=>x.id)).size===v.widgets.length;
const recover=(v:unknown)=>{const src=record(v)?v:{};let recovered=!record(v);const layouts={} as WorkspaceLayouts;for(const id of WORKSPACE_IDS){const x=src[id];if(isWorkspaceLayout(x,id))layouts[id]={id,widgets:x.widgets.map(y=>({...y}))};else{layouts[id]=getWorkspacePreset(id);recovered=true}}return{layouts,recovered}};
const encode=(layouts:WorkspaceLayouts)=>JSON.stringify({schemaVersion:WORKSPACE_SCHEMA_VERSION,layouts});
export const saveWorkspaceLayouts=(s:StorageAdapter,l:WorkspaceLayouts)=>{try{s.setItem(WORKSPACE_STORAGE_KEY,encode(recover(l).layouts));return true}catch{return false}};
export const resetWorkspaceLayouts=(s:StorageAdapter)=>{const l=getWorkspacePresets();try{s.setItem(WORKSPACE_STORAGE_KEY,encode(l))}catch{}return l};
export const loadWorkspaceLayouts=(s:StorageAdapter):WorkspaceLoadResult=>{let raw:string|null;try{raw=s.getItem(WORKSPACE_STORAGE_KEY)}catch{return{layouts:getWorkspacePresets(),recovered:true,reason:"storage-error"}}if(raw===null)return{layouts:resetWorkspaceLayouts(s),recovered:false,reason:"missing"};let p:unknown;try{p=JSON.parse(raw)}catch{return{layouts:resetWorkspaceLayouts(s),recovered:true,reason:"invalid"}}if(!record(p))return{layouts:resetWorkspaceLayouts(s),recovered:true,reason:"invalid"};const legacy=p.schemaVersion===undefined,current=p.schemaVersion===1&&record(p.layouts);if(!legacy&&!current)return{layouts:resetWorkspaceLayouts(s),recovered:true,reason:"invalid"};const r=recover(legacy?p:p.layouts);if(legacy||r.recovered)saveWorkspaceLayouts(s,r.layouts);return{layouts:r.layouts,recovered:legacy||r.recovered,reason:legacy||r.recovered?"migrated":"stored"}};
