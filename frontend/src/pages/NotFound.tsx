import { useNavigate } from "react-router-dom";
import { Button } from "@/ui/Button";
import { Card, PageHeader } from "@/ui/layout";
import { EmptyState } from "@/ui/status";

export function NotFound() {
  const navigate = useNavigate();
  return (
    <div className="grid gap-7 px-4 py-5 md:px-8 md:py-7">
      <PageHeader crumbs={[{ label: "Not found" }]} title="Not found" />
      <Card className="px-5">
        <EmptyState title="Nothing here." actions={<Button variant="primary" onClick={() => navigate("/")}>Back to Live</Button>} />
      </Card>
    </div>
  );
}
