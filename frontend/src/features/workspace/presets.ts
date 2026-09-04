import type { WidgetId, WidgetPlacement, WorkspaceId, WorkspaceLayout, WorkspaceLayouts } from "./types";
const w=(id:WidgetId,x:number,y:number,width:number,height:number):WidgetPlacement=>({id,x,y,width,height});
export const WORKSPACE_PRESETS:Readonly<WorkspaceLayouts>={
 trading:{id:"trading",widgets:[w("market-chart",0,0,8,6),w("scanner",8,0,4,6),w("positions",0,6,7,4),w("order-entry",7,6,5,4)]},
 research:{id:"research",widgets:[w("market-chart",0,0,8,6),w("market-context",8,0,4,3),w("decision-funnel",8,3,4,3),w("signal-history",0,6,12,4)]},
 risk:{id:"risk",widgets:[w("risk-overview",0,0,4,4),w("exposure",4,0,4,4),w("reconciliation",8,0,4,4),w("equity-curve",0,4,8,5),w("drawdown",8,4,4,5),w("system-events",0,9,12,3)]},
};
export const cloneWorkspaceLayout=(layout:WorkspaceLayout):WorkspaceLayout=>({id:layout.id,widgets:layout.widgets.map(x=>({...x}))});
export const getWorkspacePreset=(id:WorkspaceId)=>cloneWorkspaceLayout(WORKSPACE_PRESETS[id]);
export const getWorkspacePresets=():WorkspaceLayouts=>({trading:getWorkspacePreset("trading"),research:getWorkspacePreset("research"),risk:getWorkspacePreset("risk")});
