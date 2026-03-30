from swarm_core.reporting import DailyReportGenerator


def main():
    generator = DailyReportGenerator(repo_dir='.')
    path = generator.generate()
    print(f"Daily report generated: {path}")


if __name__ == '__main__':
    main()
