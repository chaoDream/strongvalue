// Local OCR only. Bounding boxes refer to the rendered PDF crop, never design units.
import Foundation
import AppKit
import PDFKit
import Vision

do {
    guard CommandLine.arguments.count == 3,
          let doc = PDFDocument(url: URL(fileURLWithPath: CommandLine.arguments[1])),
          let number = Int(CommandLine.arguments[2]), number > 0,
          let page = doc.page(at: number - 1) else { throw NSError(domain: "PDF", code: 1) }
    let bounds = page.bounds(for: .cropBox)
    guard bounds.width > 0, bounds.height > 0 else { throw NSError(domain: "PDF", code: 2) }
    // PDFKit renders the displayed crop and applies /Rotate. Limit each side to 2400px.
    let image = page.thumbnail(of: NSSize(width: 2400, height: 2400), for: .cropBox)
    var rect = NSRect(origin: .zero, size: image.size)
    guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil) else { throw NSError(domain: "PDF", code: 3) }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    try VNImageRequestHandler(cgImage: cg, options: [:]).perform([request])
    var lines: [[String: Any]] = []
    for observation in request.results ?? [] {
        guard let text = observation.topCandidates(1).first else { continue }
        guard lines.count < 2000, text.string.count <= 2000 else { throw NSError(domain: "OCRLimit", code: 1) }
        let b = observation.boundingBox
        lines.append(["text": text.string, "confidence": Double(text.confidence),
                      "bbox": [b.minX, 1-b.maxY, b.width, b.height]])
    }
    let result: [String: Any] = ["lines": lines, "revision": request.revision,
                                "width": cg.width, "height": cg.height]
    let output = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
    FileHandle.standardOutput.write(output)
} catch {
    FileHandle.standardError.write(Data("Local OCR could not process this page\n".utf8))
    exit(1)
}
