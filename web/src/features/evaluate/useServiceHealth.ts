import { useQuery } from "@tanstack/react-query";
import { healthApi } from "@/api/endpoints/health";
import { serviceSnapshotOf, type ServiceSnapshot } from "./serviceHealth";

/**
 * The one health query for the app. The header pill and the Evaluate hero both
 * read it through this object, so they share a cache entry and make one request
 * per refresh, not one per mounted consumer.
 */
export const SERVICE_HEALTH_QUERY = {
  queryKey: ["service-health"],
  queryFn: healthApi.get,
  // Asked again every 60 s while the page is visible, so an open tab does not
  // keep showing an answer from long ago.
  refetchInterval: 60_000,
  refetchIntervalInBackground: false,
};

/** The service's status and models, read from the shared health query. */
export function useServiceSnapshot(): ServiceSnapshot {
  const query = useQuery(SERVICE_HEALTH_QUERY);
  return serviceSnapshotOf({ data: query.data, isError: query.isError });
}
