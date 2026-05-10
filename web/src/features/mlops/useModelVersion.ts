import { useQuery } from "@tanstack/react-query";
import { fetchModelVersion } from "../../api/model";

export function useModelVersion() {
  return useQuery({
    queryKey: ["model", "version"],
    queryFn: fetchModelVersion,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
}
