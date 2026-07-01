import argparse
import os

from detection.ambiguity_detector import AmbiguityDetector
from selection.auto_selector import AutoSelector
from selection.user_selector import UserSelector
from rewriting.question_rewriter import QuestionRewriter


def run(args):
    detector = AmbiguityDetector(args)
    if args.batch:
        detector.detect_batch()
    else:
        detector.detect()

    if args.auto_select:
        AutoSelector(args).select()
    else:
        UserSelector(args).select()

    QuestionRewriter(args).rewrite()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    
    parser.add_argument('--dataset', type=str, required=True)
    parser.add_argument('--input_path', type=str, required=True)
    parser.add_argument('--output_path', type=str, required=True)
    
    parser.add_argument('--llm_model', type=str, default='gpt-4o')
    parser.add_argument('--batch', action='store_true')
    parser.add_argument('--resume', action='store_true')
    
    parser.add_argument('--auto_select', action='store_true')
    
    args = parser.parse_args()

    os.makedirs(args.output_path, exist_ok=True)
    
    args.detection_path = os.path.join(args.output_path, "detection.json")
    args.rewriting_path = os.path.join(args.output_path, "rewriting.json")

    print("=" * 50)
    print("Configuration:")
    for key, value in vars(args).items():
        print(f"  {key}: {value}")
    print("=" * 50)
    
    run(args)
