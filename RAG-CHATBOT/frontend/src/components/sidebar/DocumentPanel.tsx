"use client";

import { useApp } from "@/context/AppContext";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useDropzone } from "react-dropzone";
import {
  Upload,
  FileText,
  Trash2,
  CheckCircle,
  AlertCircle,
  Clock,
} from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { apiService } from "@/lib/api";

export function DocumentPanel() {
  const {
    documents,
    isUploading,
    addDocument,
    updateDocumentStatus,
    setIsUploading,
    removeDocument,
  } = useApp();

  const onDrop = async (acceptedFiles: File[]) => {
    for (const file of acceptedFiles) {
      if (file.size > 10 * 1024 * 1024) {
        // 10MB limit
        toast.error(`File ${file.name} is too large. Maximum size is 10MB.`);
        continue;
      }

      if (!file.type.includes("pdf")) {
        toast.error(
          `File ${file.name} is not a PDF. Only PDF files are supported.`
        );
        continue;
      }

      // Create placeholder local doc while uploading
      const placeholderId = `doc_${Date.now()}_${Math.random()
        .toString(36)
        .substr(2, 9)}`;
      const newDoc = {
        document_id: placeholderId,
        filename: file.name,
        size: file.size,
        upload_timestamp: new Date().toISOString(),
        status: "uploading" as const,
      };

      addDocument(newDoc);
      setIsUploading(true);

      try {
        const uploadResp = await apiService.uploadDocument(file, true);
        // uploadResp is UploadResponse, not an array
        const serverId = uploadResp.document_id || placeholderId;
        // Remove placeholder and add backend doc
        removeDocument(placeholderId);
        addDocument({
          document_id: uploadResp.document_id,
          filename: uploadResp.filename,
          size: uploadResp.size || file.size,
          upload_timestamp: new Date().toISOString(),
          status:
            uploadResp.status === "ready" || uploadResp.status === "completed"
              ? "ready"
              : uploadResp.status === "error"
              ? "error"
              : "processing",
        });
        // poll for status
        const pollStatus = async () => {
          try {
            const statusDoc = await apiService.getDocumentStatus(serverId);
            updateDocumentStatus(
              serverId,
              statusDoc.status === "ready" || statusDoc.status === "completed"
                ? "ready"
                : statusDoc.status === "error"
                ? "error"
                : "processing"
            );
            if (
              statusDoc.status === "ready" ||
              statusDoc.status === "completed"
            ) {
              toast.success(`Document ${file.name} processed successfully!`);
              return;
            }
            if (statusDoc.status === "error") {
              toast.error(`Processing failed for ${file.name}`);
              return;
            }
            setTimeout(pollStatus, 1500);
          } catch (error) {
            console.error(error);
            updateDocumentStatus(serverId, "error");
            toast.error(`Failed to check status for ${file.name}`);
          }
        };
        setTimeout(pollStatus, 800);
      } catch (error) {
        console.error(error);
        updateDocumentStatus(placeholderId, "error");
        toast.error(`Upload failed for ${file.name}`);
      } finally {
        setIsUploading(false);
      }
    }
  };

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { "application/pdf": [".pdf"] },
    maxSize: 10 * 1024 * 1024, // 10MB
    disabled: isUploading,
  });

  const getStatusIcon = (status: string) => {
    switch (status) {
      case "ready":
        return <CheckCircle className="h-3 w-3 text-green-500" />;
      case "processing":
        return <Clock className="h-3 w-3 text-blue-500" />;
      case "error":
        return <AlertCircle className="h-3 w-3 text-red-500" />;
      default:
        return <Upload className="h-3 w-3 text-muted-foreground" />;
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "ready":
        return (
          <Badge
            variant="secondary"
            className="bg-green-100 text-green-800 text-[11px] px-2 py-0.5"
          >
            Ready
          </Badge>
        );
      case "processing":
        return (
          <Badge
            variant="secondary"
            className="bg-blue-100 text-blue-800 text-[11px] px-2 py-0.5"
          >
            Processing
          </Badge>
        );
      case "error":
        return (
          <Badge variant="destructive" className="text-[11px] px-2 py-0.5">
            Err
          </Badge>
        );
      default:
        return (
          <Badge variant="outline" className="text-[11px] px-2 py-0.5">
            Up
          </Badge>
        );
    }
  };

  return (
    <div className="p-4">
      <h3 className="font-medium text-sm text-muted-foreground uppercase tracking-wide mb-4">
        Documents
      </h3>

      {/* Upload Area */}
      <div
        {...getRootProps()}
        className={`border-2 border-dashed rounded-lg p-4 mb-4 cursor-pointer transition-colors ${
          isDragActive
            ? "border-primary bg-primary/5"
            : "border-muted-foreground/25 hover:border-muted-foreground/50"
        } ${isUploading ? "opacity-50 cursor-not-allowed" : ""}`}
      >
        <input {...getInputProps()} />
        <div className="text-center">
          <Upload className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            {isDragActive ? "Drop PDFs here..." : "Drag & drop PDFs here"}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Max 10MB per file
          </p>
        </div>
      </div>

      {/* Documents List */}
      <ScrollArea className="h-[200px] pr-2">
        {documents.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            <FileText className="h-8 w-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm">No documents uploaded.</p>
            <p className="text-xs">Upload PDFs to get started.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {documents.map((doc) => (
              <div
                key={doc.document_id}
                className="flex items-center justify-between px-2 py-1 rounded-md border bg-card w-full overflow-hidden"
              >
                <div className="flex items-center space-x-2 flex-1 min-w-0">
                  <div className="flex-shrink-0 mr-1">
                    {getStatusIcon(doc.status)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate max-w-[120px] sm:max-w-[140px] lg:max-w-[160px]">
                      {doc.filename}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {(() => {
                        const ts = doc.upload_timestamp;
                        const d = ts ? new Date(ts) : null;
                        if (d && !Number.isNaN(d.getTime())) {
                          return formatDistanceToNow(d, { addSuffix: true });
                        }
                        return "unknown time";
                      })()}{" "}
                      {doc.size > 0 ? (
                        <>•{(doc.size / 1024 / 1024).toFixed(1)}MB</>
                      ) : doc.num_chunks ? (
                        <>•{doc.num_chunks} chunks</>
                      ) : (
                        <>•unknown size</>
                      )}
                    </p>
                  </div>
                </div>
                <div
                  className="flex items-center   space-x-2 flex-shrink-0 ml-3"
                  style={{ minWidth: 80 }}
                >
                  <div className="flex-shrink-0" title={doc.status}>
                    {getStatusBadge(doc.status)}
                  </div>
                  <div className="flex-shrink-0">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                      onClick={async () => {
                        try {
                          await apiService.deleteDocument(doc.document_id);
                          // remove from context
                          removeDocument(doc.document_id);
                          toast.success("Document deleted");
                        } catch (error) {
                          console.error(error);
                          toast.error("Failed to delete document");
                        }
                      }}
                    >
                      <Trash2 className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
