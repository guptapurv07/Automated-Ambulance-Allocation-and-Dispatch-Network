import type { Hospital } from "@/api/client";
import { useAmbulances, useHospitals } from "@/api/queries";
import { coord } from "@/lib/format";
import { Button } from "@/ui/Button";
import { PageHeader } from "@/ui/layout";
import { Banner, Skeleton } from "@/ui/status";
import { DataTable, type Column } from "@/ui/Table";

/** The hospitals, their beds, and how many ambulances are handing a patient over at each one now. */
export default function Hospitals() {
  const hospitals = useHospitals();
  const ambulances = useAmbulances().data ?? [];
  const at = (h: Hospital) => ambulances.filter((a) => a.status === "AT_HOSPITAL" && Math.abs(a.current_lat - h.lat) < 1e-4 && Math.abs(a.current_lon - h.lon) < 1e-4).length;
  const columns: Column<Hospital>[] = [
    { key: "name", header: "Hospital", width: "minmax(220px,1fr)", sortValue: (h) => h.name, cell: (h) => <span title={h.name}>{h.name}</span> },
    { key: "beds", header: "Beds", width: "100px", align: "right", numeric: true, sortValue: (h) => h.bed_capacity, cell: (h) => h.bed_capacity },
    { key: "here", header: "Handing over", width: "150px", align: "right", numeric: true, sortValue: (h) => at(h), cell: (h) => at(h) || "—" },
    { key: "pos", header: "Position", width: "170px", align: "right", numeric: true, cell: (h) => coord(h.lat, h.lon) },
  ];

  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "Hospitals" }]} title="Hospitals" />
      {hospitals.isPending && <Skeleton className="h-60" />}
      {hospitals.isError && (
        <Banner kind="error" title="Cannot load the hospitals." actions={<Button size="sm" variant="secondary" onClick={() => hospitals.refetch()}>Retry</Button>}>
          {(hospitals.error as Error).message}
        </Banner>
      )}
      {hospitals.data && (
        <div className="overflow-x-auto"><div className="min-w-[640px]">
          <DataTable label="Hospitals" rows={hospitals.data} columns={columns} rowKey={(h) => h.id} fill />
        </div></div>
      )}
    </div>
  );
}
