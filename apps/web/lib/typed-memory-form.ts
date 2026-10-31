export type MemoryFormKind = "free_text" | "place" | "commute_preference" | "time_preference" | "experience";
type Field = {key: string; label: string; type?: string; optional?: boolean; options?: [string,string][]};
export const memoryKinds: [MemoryFormKind,string][] = [["free_text","普通文字"],["place","常用地点"],
  ["commute_preference","通勤偏好"],["time_preference","时间偏好"],["experience","经历与经验"]];
export const memoryFields: Record<MemoryFormKind, Field[]> = {
  free_text: [],
  place: [{key:"label",label:"地点名称"},{key:"address",label:"地址"},
    {key:"coordinate_system",label:"坐标系",options:[["WGS84","WGS84"],["GCJ02","GCJ02（高德等）"],["BD09","BD09（百度）"]]},
    {key:"latitude",label:"纬度",type:"number",optional:true},{key:"longitude",label:"经度",type:"number",optional:true}],
  commute_preference: [{key:"origin",label:"出发地"},{key:"destination",label:"目的地"},
    {key:"travel_mode",label:"出行方式",options:[["walk","步行"],["bicycle","骑行"],["transit","公共交通"],["drive","驾车"]]},
    {key:"timezone",label:"时区（例如 Asia/Shanghai）"},{key:"weekdays",label:"重复星期（周一为1，周日为7，用逗号分隔）"},
    {key:"preferred_departure",label:"偏好出发时间",type:"time",optional:true}],
  time_preference: [{key:"activity",label:"活动"},{key:"preferred_time",label:"偏好时间",type:"time"},
    {key:"timezone",label:"时区（例如 Asia/Shanghai）"}],
  experience: [{key:"occurred_on",label:"发生日期",type:"date"},{key:"description",label:"经历描述"},
    {key:"lesson",label:"经验或教训",optional:true}],
};

/** Build explicit form fields; backend remains authoritative for dates, ranges and permissions. */
export function buildTypedMemory(kind: MemoryFormKind, values: Record<string,string>): Record<string,unknown> | undefined {
  if (kind === "free_text") return undefined;
  const result: Record<string,unknown> = {memory_type:kind};
  for (const field of memoryFields[kind]) {
    const value = values[field.key]?.trim() ?? "";
    if (!value && !field.optional) throw new Error(`请填写${field.label}。`);
    if (!value) continue;
    if (field.options && !field.options.some(([key]) => key === value)) throw new Error(`请选择${field.label}。`);
    result[field.key] = value;
  }
  if (kind === "place") {
    if ((result.latitude === undefined) !== (result.longitude === undefined)) throw new Error("经度与纬度必须同时填写，或同时留空。");
    for (const key of ["latitude", "longitude"]) if (result[key] !== undefined) {
      const value = Number(result[key]);
      if (!Number.isFinite(value) || Math.abs(value) > (key === "latitude" ? 90 : 180)) throw new Error("经纬度超出有效范围。");
      result[key] = value;
    }
  }
  if (kind === "commute_preference") {
    const days = String(result.weekdays).split(/[,，]/).map(day => Number(day.trim()));
    if (days.some(day => !Number.isInteger(day) || day < 1 || day > 7) || new Set(days).size !== days.length) throw new Error("请选择不重复的星期1至7。");
    result.weekdays = days;
  }
  return result;
}
