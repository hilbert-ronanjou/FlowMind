import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ErrorState({ message, retry }: { message: string; retry: () => void }) {
  return (
    <div className="flex min-h-56 flex-col items-center justify-center rounded-2xl border border-red-100 bg-red-50/60 p-8 text-center">
      <AlertCircle className="h-8 w-8 text-red-600" />
      <h3 className="mt-3 font-semibold text-red-950">We couldn&apos;t load this page</h3>
      <p className="mt-1 text-sm text-red-700">{message}</p>
      <Button className="mt-5" variant="outline" onClick={retry}>Try again</Button>
    </div>
  );
}

