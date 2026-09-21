import UIKit

/// Shown when the phone cannot reach Omni. Says what happened in plain words
/// and offers the one useful action.
final class OfflineView: UIView {
    private let retry: () -> Void

    init(retry: @escaping () -> Void) {
        self.retry = retry
        super.init(frame: .zero)
        backgroundColor = Brand.navy

        let title = UILabel()
        title.text = "No connection"
        title.font = UIFont(name: "Palatino-Roman", size: 30) ?? .systemFont(ofSize: 30)
        title.textColor = .white
        title.textAlignment = .center

        let body = UILabel()
        body.text = "Omni could not be reached. Check your signal or Wi-Fi, then try again."
        body.font = .systemFont(ofSize: 16)
        body.textColor = UIColor(white: 0.78, alpha: 1)
        body.numberOfLines = 0
        body.textAlignment = .center

        let button = UIButton(type: .system)
        button.setTitle("Try again", for: .normal)
        button.titleLabel?.font = .systemFont(ofSize: 17, weight: .semibold)
        button.setTitleColor(Brand.navy, for: .normal)
        button.backgroundColor = Brand.orange
        button.layer.cornerRadius = 12
        button.addTarget(self, action: #selector(tapped), for: .touchUpInside)
        button.heightAnchor.constraint(equalToConstant: 50).isActive = true
        button.widthAnchor.constraint(equalToConstant: 180).isActive = true

        let stack = UIStackView(arrangedSubviews: [title, body, button])
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 16
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            stack.leadingAnchor.constraint(equalTo: leadingAnchor, constant: 32),
            stack.trailingAnchor.constraint(equalTo: trailingAnchor, constant: -32),
        ])
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    @objc private func tapped() { retry() }
}
