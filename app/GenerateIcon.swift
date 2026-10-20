import AppKit

// 生成 App 图标：Claude 风格的放射星芒 + 右下角的解锁徽章。
// 星芒代表 Claude，开口的锁代表“只解除可用性限制”。
// 与菜单栏图标共用同一套形状，见 NativeUnlock.swift 里的同名函数。

/// 放射星芒：rays 条从中心向外张开的圆头花瓣。
func starburstPath(center: NSPoint, radius: CGFloat, rays: Int,
                   innerRatio: CGFloat = 0.20,
                   innerWidth: CGFloat = 0.075,
                   outerWidth: CGFloat = 0.21) -> NSBezierPath {
    let path = NSBezierPath()
    let r0 = radius * innerRatio
    let w0 = radius * innerWidth / 2
    let w1 = radius * outerWidth / 2
    let tipY = radius - w1
    for index in 0..<rays {
        let petal = NSBezierPath()
        petal.move(to: NSPoint(x: -w0, y: r0))
        petal.curve(to: NSPoint(x: -w1, y: tipY),
                    controlPoint1: NSPoint(x: -w0 * 1.25, y: r0 + (radius - r0) * 0.45),
                    controlPoint2: NSPoint(x: -w1, y: tipY - (radius - r0) * 0.30))
        petal.appendArc(withCenter: NSPoint(x: 0, y: tipY), radius: w1,
                        startAngle: 180, endAngle: 0, clockwise: true)
        petal.curve(to: NSPoint(x: w0, y: r0),
                    controlPoint1: NSPoint(x: w1, y: tipY - (radius - r0) * 0.30),
                    controlPoint2: NSPoint(x: w0 * 1.25, y: r0 + (radius - r0) * 0.45))
        petal.close()
        var transform = AffineTransform()
        transform.translate(x: center.x, y: center.y)
        transform.rotate(byDegrees: CGFloat(index) * 360 / CGFloat(rays))
        petal.transform(using: transform)
        path.append(petal)
    }
    return path
}

/// 挂锁：锁体填充 + 锁梁描边；open = true 时锁梁右侧抬起。
func drawPadlock(rect: NSRect, open: Bool) {
    let width = rect.width
    let stroke = width * 0.15
    let shackleRadius = width * 0.30
    let bodyHeight = max(rect.height - shackleRadius - stroke, width * 0.40)
    let body = NSRect(x: rect.minX, y: rect.minY, width: width, height: bodyHeight)
    NSBezierPath(roundedRect: body, xRadius: width * 0.22, yRadius: width * 0.22).fill()

    let shackle = NSBezierPath()
    shackle.appendArc(withCenter: NSPoint(x: rect.midX, y: body.maxY),
                      radius: shackleRadius, startAngle: 180,
                      endAngle: open ? 45 : 0, clockwise: true)
    shackle.lineWidth = stroke
    shackle.lineCapStyle = .round
    shackle.stroke()
}

/// 在 1024×1024 的点坐标系里画图标。
func drawAppIcon() {
    let squircle = NSBezierPath(roundedRect: NSRect(x: 44, y: 44, width: 936, height: 936),
                                xRadius: 216, yRadius: 216)
    NSGradient(starting: NSColor(red: 0.90, green: 0.51, blue: 0.37, alpha: 1),
               ending: NSColor(red: 0.66, green: 0.27, blue: 0.23, alpha: 1))!
        .draw(in: squircle, angle: -72)

    let badge = NSRect(x: 596, y: 152, width: 316, height: 316)
    // 星芒按“整块画布挖掉徽章圆”裁剪，徽章周围留出一圈背景色，两者不会粘在一起。
    let clip = NSBezierPath(rect: NSRect(x: 0, y: 0, width: 1024, height: 1024))
    clip.append(NSBezierPath(ovalIn: badge.insetBy(dx: -22, dy: -22)))
    clip.windingRule = .evenOdd
    NSGraphicsContext.saveGraphicsState()
    clip.setClip()
    NSColor(white: 1, alpha: 0.98).setFill()
    starburstPath(center: NSPoint(x: 466, y: 588), radius: 318, rays: 12,
                  innerWidth: 0.065, outerWidth: 0.195).fill()
    NSGraphicsContext.restoreGraphicsState()

    NSColor(white: 1, alpha: 0.98).setFill()
    NSBezierPath(ovalIn: badge).fill()
    NSColor(red: 0.70, green: 0.30, blue: 0.25, alpha: 1).setFill()
    NSColor(red: 0.70, green: 0.30, blue: 0.25, alpha: 1).setStroke()
    drawPadlock(rect: badge.insetBy(dx: 80, dy: 86), open: true)
}

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)

for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        NSGraphicsContext.current!.cgContext
            .scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
        drawAppIcon()
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let name = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
        try bitmap.representation(using: .png, properties: [:])!
            .write(to: destination.appendingPathComponent(name))
    }
}
