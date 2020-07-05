from model.data_utils import CoNLLDataset
from model.ner_model import NERModel
from model.config import Config
import argparse
import os
import time
 
parser = argparse.ArgumentParser(allow_abbrev=False)
parser.add_argument("--dim_char", type=int, default=100, help="character embedding size", dest='dim_char')
parser.add_argument("--hidden_size_char", type=int, default=100, help="character lstm hidden dim", dest='hidden_size_char')
parser.add_argument("--hidden_size_lstm_1", type=int, default=300, help="lstm_1 hidden dim", dest='hidden_size_lstm_1')
parser.add_argument("--hidden_size_lstm_2", type=int, default=300, help="lstm_1 hidden dim", dest='hidden_size_lstm_2')
parser.add_argument("--batch_sample", type=int, default=228, help="negtive samples", dest='batch_sample')
parser.add_argument("--elmo_scale", type=float, default=3.35, help="elmo scale", dest='elmo_scale')
parser.add_argument("--lr_method", type=str, default='adam', help="optimizer", dest='lr_method')
parser.add_argument("--batch_size", type=int, default=20, help="batch_size", dest='batch_size')
parser.add_argument("--learning_rate", type=float, default=0.001, help="learning rate", dest='learning_rate')
parser.add_argument("--decay_logic", type=bool, default=True, help="decay_logic", dest='decay_logic')
parser.add_argument("--gpu", type=str, default='0', help="gpu", dest='gpu')
parser.add_argument("--run_name", type=str, default='df_run_name', help="run_name", dest='run_name')
 
arg = parser.parse_args()
print(arg)
os.environ["CUDA_VISIBLE_DEVICES"]=arg.gpu
 
def main():
    # print("start time:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
 
    # # create instance of config
    config = Config()
    config.dim_char = arg.dim_char
    config.hidden_size_char = arg.hidden_size_char
    config.hidden_size_lstm_1 = arg.hidden_size_lstm_1
    config.hidden_size_lstm_2 = arg.hidden_size_lstm_2
    config.batch_sample = arg.batch_sample
    config.elmo_scale = arg.elmo_scale
    config.lr_method = arg.lr_method
    config.batch_size = arg.batch_size
    config.learning_rate = arg.learning_rate
    config.decay_logic = arg.decay_logic
    config.run_name = arg.run_name
 
    # build model
    model = NERModel(config)
    model.build()
 
    # create datasets
    dev   = CoNLLDataset(config.filename_dev, config.elmofile_dev, config.bertfile_dev, config.processing_word,
                         config.processing_postags, config.generate_anchor,
                         config.max_iter)
    train = CoNLLDataset(config.filename_train, config.elmofile_train, config.bertfile_train, config.processing_word,
                         config.processing_postags, config.generate_anchor,
                         config.max_iter)
    # dev = CoNLLDataset(config.filename_test, config.elmofile_test, config.processing_word,
    #                      config.processing_postags, config.generate_anchor,
    #                      config.max_iter)
    #train model
    model.train(train, dev)
    print("end time:", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()))
 
if __name__ == "__main__":
    main()