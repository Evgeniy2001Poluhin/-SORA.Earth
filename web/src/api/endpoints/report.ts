const BASE = "";
export const reportApi = {
  pdf: async (body: any): Promise<Blob> => {
    const res = await fetch(BASE + "/api/v1/report/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error("pdf failed");
    return res.blob();
  },
};
