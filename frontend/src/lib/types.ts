// Friendly DTO aliases over the generated OpenAPI types (src/lib/api-types.ts).
// Regenerate the source after changing backend/api.py:
//   cd backend && uv run python -c "import json,main,pathlib; pathlib.Path('openapi.json').write_text(json.dumps(main.app.openapi(),indent=2)+chr(10))"
//   cd frontend && npm run gen:types
import type { components } from './api-types';

type S = components['schemas'];

export type Part = S['PartOut'];
export type BomLine = S['BomLineOut'];
export type BomUsage = S['BomUsageOut'];
export type StockItem = S['StockItemOut'];
export type Supplier = S['SupplierOut'];
export type SupplierPart = S['SupplierPartOut'];
export type PurchaseOrder = S['PurchaseOrderOut'];
export type PartPurchaseOrder = S['PartPurchaseOrderOut'];
export type POLine = S['POLineOut'];
export type BuildOrder = S['BuildOrderOut'];
export type PartBuild = S['PartBuildOut'];
export type StockLogEntry = S['StockLogEntryOut'];
export type BuildLine = S['BuildLineOut'];
export type Setting = S['SettingOut'];
export type Activity = S['ActivityOut'];
export type ActivityRef = S['ActivityRef'];
export type SearchResult = S['SearchResult'];
export type StockValueReport = S['StockValueReport'];
export type StockValueRow = S['StockValueRow'];
