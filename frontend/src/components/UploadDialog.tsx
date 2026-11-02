import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export function UploadDialog() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const mutation = useMutation({
    mutationFn: async (f: File) => {
      const buffer = await f.arrayBuffer();
      const res = await fetch(
        `/api/v1/documents?filename=${encodeURIComponent(f.name)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/pdf" },
          body: buffer,
        },
      );
      if (!res.ok) throw new Error("Upload failed");
      return res.json();
    },
    onSuccess: () => {
      setOpen(false);
      setFile(null);
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
      void queryClient.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (file) mutation.mutate(file);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Upload className="mr-2 h-4 w-4" />
          Upload PDF
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Upload a PDF document</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            ref={inputRef}
            type="file"
            accept=".pdf"
            className="block w-full text-sm"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {mutation.isError && (
            <p className="text-sm text-[hsl(var(--destructive))]">
              Upload failed. Please try again.
            </p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={!file || mutation.isPending}>
              {mutation.isPending ? "Uploading…" : "Upload"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
